"""Unit tests for scrapers/ core modules (encoding, extractors, storage, merger, and base adapter)."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import httpx

from scrapers.base import HenanHrssAdapter
from scrapers.core.extractors import (
    extract_attachment,
    extract_docx,
    extract_html,
    extract_pdf,
    extract_xlsx,
)
from scrapers.core.http_client import PoliteHttpClient, decode_html_bytes, detect_encoding
from scrapers.core.merger import (
    archive_expired_jobs,
    compute_job_fingerprint,
    expire_outdated_jobs,
    merge_jobs_into_dataset,
)
from scrapers.core.storage import CrawlStateStore, compute_content_hash
from scripts.validate_data import validate_workspace
from tests.fixtures.fixture_builders import (
    build_legacy_ole2_bytes,
    ensure_binary_fixtures,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class ScrapersCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.binary_paths = ensure_binary_fixtures(FIXTURES_DIR)
        cls.html_utf8_text = (FIXTURES_DIR / "henan_announcement_utf8.html").read_text(encoding="utf-8")
        cls.list_html_text = (FIXTURES_DIR / "henan_list_page.html").read_text(encoding="utf-8")
        cls.sample_batch = json.loads((FIXTURES_DIR / "sample_jobs_batch.json").read_text(encoding="utf-8"))

    def test_encoding_detection_utf8_gbk_gb18030(self) -> None:
        # 1. UTF-8 HTML
        utf8_bytes = self.html_utf8_text.encode("utf-8")
        enc, text = decode_html_bytes(utf8_bytes, content_type="text/html; charset=utf-8")
        self.assertEqual(enc, "utf-8")
        self.assertIn("2026年河南省直属事业单位公开招聘工作人员公告", text)

        # 2. GBK / GB2312 HTML with misleading ISO-8859-1 HTTP header
        gbk_html = (
            '<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312"></head>'
            "<body><h1>河南省事业单位公开招聘专栏</h1></body></html>"
        )
        gbk_bytes = gbk_html.encode("gbk")
        enc_gbk, text_gbk = decode_html_bytes(gbk_bytes, content_type="text/html; charset=ISO-8859-1")
        self.assertIn(enc_gbk, {"gbk", "gb18030"})
        self.assertIn("河南省事业单位公开招聘专栏", text_gbk)

        # 3. GB18030 4-byte rare CJK character (e.g. 𠀀 U+20000)
        gb18030_html = (
            '<html><head><meta charset="gb18030"></head>'
            "<body><p>招聘联系人：张𠀀（生僻字测试）</p></body></html>"
        )
        gb18030_bytes = gb18030_html.encode("gb18030")
        self.assertEqual(detect_encoding(gb18030_bytes), "gb18030")
        _, text_18030 = decode_html_bytes(gb18030_bytes)
        self.assertIn("张𠀀（生僻字测试）", text_18030)

    def test_http_client_rate_limit_and_retry(self) -> None:
        attempts = 0
        sleeps: list[float] = []
        clock_val = [100.0]

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock_val[0] += seconds

        def fake_clock() -> float:
            return clock_val[0]

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(503, content=b"Service Unavailable")
            body = "<html><head><meta charset='gbk'></head><body>河南人事考试网</body></html>".encode("gbk")
            return httpx.Response(200, headers={"Content-Type": "text/html"}, content=body)

        transport = httpx.MockTransport(handler)
        with PoliteHttpClient(
            min_interval_sec=1.5,
            max_retries=2,
            backoff_factor=0.25,
            transport=transport,
            sleep_fn=fake_sleep,
            clock_fn=fake_clock,
        ) as client:
            resp1 = client.get("http://www.hnrsks.com/test1.html")
            self.assertEqual(resp1.status_code, 200)
            self.assertIn("河南人事考试网", resp1.text)
            self.assertEqual(attempts, 2)
            # Immediate second request to same host triggers polite rate-limit sleep
            resp2 = client.get("http://www.hnrsks.com/test2.html")
            self.assertEqual(resp2.status_code, 200)
            self.assertTrue(any(abs(s - 1.5) < 1e-6 for s in sleeps))

    def test_extract_html_and_attachments(self) -> None:
        base_url = "https://hrss.henan.gov.cn/2026/1118/announce_1001.html"
        res = extract_html(self.html_utf8_text, base_url=base_url)

        self.assertEqual(res.title, "2026年河南省直属事业单位公开招聘工作人员公告")
        self.assertEqual(res.published_at, "2026-11-18")
        self.assertNotIn("noisy inline script", res.text)
        self.assertIn("| 网上报名 | 2026-11-20 至 2026-11-26 | 登录河南人事考试网提交申请 |", res.text)
        self.assertEqual(len(res.tables), 1)
        self.assertEqual(len(res.attachments), 5)

        att_types = [a.file_type for a in res.attachments]
        self.assertEqual(att_types, ["xlsx", "docx", "pdf", "doc", "xls"])
        self.assertEqual(
            res.attachments[0].url,
            "https://hrss.henan.gov.cn/upload/202611/henan_2026_posts.xlsx",
        )
        self.assertEqual(
            res.attachments[1].url,
            "https://hrss.henan.gov.cn/2026/1118/attachments/henan_2026_guide.docx",
        )

    def test_extract_xlsx_docx_pdf_and_legacy_degradation(self) -> None:
        # 1. XLSX
        xlsx_res = extract_xlsx(self.binary_paths["xlsx"])
        self.assertTrue(xlsx_res.supported)
        self.assertEqual(xlsx_res.file_type, "xlsx")
        self.assertIn("省直岗位表", xlsx_res.text)
        self.assertIn("河南省某直属事业单位", xlsx_res.text)
        self.assertIn("计算机科学与技术、软件工程", xlsx_res.text)
        self.assertEqual(len(xlsx_res.tables), 1)

        # 2. DOCX
        docx_res = extract_docx(self.binary_paths["docx"])
        self.assertTrue(docx_res.supported)
        self.assertEqual(docx_res.file_type, "docx")
        self.assertIn("报考人员须具备本科及以上学历", docx_res.text)
        self.assertIn("| 0101 | 信息技术岗 | 专业技术岗（初级） |", docx_res.text)
        self.assertEqual(len(docx_res.tables), 1)

        # 3. PDF
        pdf_res = extract_pdf(self.binary_paths["pdf"])
        self.assertTrue(pdf_res.supported)
        self.assertEqual(pdf_res.file_type, "pdf")
        self.assertIn("笔试考场规则", pdf_res.text)
        self.assertIn("职业能力测验", pdf_res.text)
        self.assertGreaterEqual(len(pdf_res.tables), 1)

        # 4. Legacy .doc and .xls graceful degradation
        doc_res = extract_attachment(self.binary_paths["doc"], "legacy_apply_form.doc")
        self.assertFalse(doc_res.supported)
        self.assertEqual(doc_res.file_type, "doc")
        self.assertIn("附件解析降级提示", doc_res.text)
        self.assertTrue(len(doc_res.warnings) > 0)

        xls_res = extract_attachment(self.binary_paths["xls"], "legacy_posts_old.xls")
        self.assertFalse(xls_res.supported)
        self.assertEqual(xls_res.file_type, "xls")
        self.assertIn("附件解析降级提示", xls_res.text)

        # 5. OLE2 binary disguised as .xlsx
        disguised_res = extract_xlsx(build_legacy_ole2_bytes(), display_name="fake.xlsx")
        self.assertFalse(disguised_res.supported)
        self.assertEqual(disguised_res.file_type, "xls")
        self.assertIn("OLE2", disguised_res.text)

    def test_storage_deduplication_and_raw_inbox_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CrawlStateStore(root_dir=root)

            url = "https://hrss.henan.gov.cn/2026/1118/announce_1001.html"
            # 1st ingest: brand new -> writes to raw_inbox
            r1 = store.ingest_announcement(
                source_id="henan_hrss_gov",
                source_name="河南省人力资源和社会保障厅",
                source_url=url,
                title="2026年河南省直属事业单位公开招聘工作人员公告",
                published_at="2026-11-18",
                body_text="正文内容：计划招聘120人。",
                attachments=[{"name": "岗位表.xlsx", "url": "https://hrss.henan.gov.cn/a.xlsx", "file_type": "xlsx"}],
                attachments_text_map={"岗位表.xlsx": "| 0101 | 信息技术岗 |"},
            )
            self.assertEqual(r1.status, "new")
            self.assertTrue(r1.written)
            self.assertIsNotNone(r1.inbox_path)
            assert r1.inbox_path is not None
            self.assertTrue(r1.inbox_path.exists())
            content = r1.inbox_path.read_text(encoding="utf-8")
            self.assertIn("source_url: https://hrss.henan.gov.cn/2026/1118/announce_1001.html", content)
            self.assertIn("published_at: 2026-11-18", content)
            self.assertIn("岗位表.xlsx", content)
            self.assertIn("| 0101 | 信息技术岗 |", content)

            # 2nd ingest with identical text (even with extra whitespace): unchanged -> skipped
            r2 = store.ingest_announcement(
                source_id="henan_hrss_gov",
                source_name="河南省人力资源和社会保障厅",
                source_url=url,
                title="2026年河南省直属事业单位公开招聘工作人员公告",
                published_at="2026-11-18",
                body_text="正文内容：计划招聘120人。   \n\n",
                attachments=[{"name": "岗位表.xlsx", "url": "https://hrss.henan.gov.cn/a.xlsx", "file_type": "xlsx"}],
                attachments_text_map={"岗位表.xlsx": "| 0101 | 信息技术岗 |"},
            )
            self.assertEqual(r2.status, "unchanged")
            self.assertFalse(r2.written)
            self.assertEqual(r1.content_hash, r2.content_hash)

            # 3rd ingest with updated attachment content: changed -> writes new file
            r3 = store.ingest_announcement(
                source_id="henan_hrss_gov",
                source_name="河南省人力资源和社会保障厅",
                source_url=url,
                title="2026年河南省直属事业单位公开招聘工作人员公告（补充）",
                published_at="2026-11-18",
                body_text="正文内容：计划招聘125人（补充公告）。",
                attachments=[{"name": "岗位表.xlsx", "url": "https://hrss.henan.gov.cn/a.xlsx", "file_type": "xlsx"}],
                attachments_text_map={"岗位表.xlsx": "| 0101 | 信息技术岗 | 扩招 |"},
            )
            self.assertEqual(r3.status, "changed")
            self.assertTrue(r3.written)
            self.assertNotEqual(r1.content_hash, r3.content_hash)

    def test_merger_fingerprint_dedup_expiration_and_monthly_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            (root / "data" / "archive").mkdir(parents=True, exist_ok=True)

            (root / "config" / "sources.json").write_text(
                json.dumps({"schema_version": "1.0", "sources": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "data" / "companies.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "updated_at": "2026-11-18T12:00:00+08:00",
                        "items": self.sample_batch["companies"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (root / "data" / "jobs.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "updated_at": "2026-11-18T12:00:00+08:00",
                        "items": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            ref_today = date(2026, 11, 18)
            initial_jobs = copy.deepcopy(self.sample_batch["jobs"])

            # 1. Initial merge without archiving: 3 inserted, 1 (2026-10-15) marked expired
            m1 = merge_jobs_into_dataset(
                initial_jobs,
                root_dir=root,
                today=ref_today,
                archive_expired=False,
            )
            self.assertEqual(m1.inserted, 3)
            self.assertEqual(m1.expired_marked, 1)
            self.assertEqual(m1.active_total, 3)

            # Verify workspace contract validation passes on merged data
            self.assertEqual(validate_workspace(root), [])

            # 2. Fingerprint deduplication test: same job with a different id and updated summary
            dup_job = copy.deepcopy(initial_jobs[0])
            dup_job["id"] = "pub-henan-duplicate-new-id"
            dup_job["summary"] = "省直全额拨款事业编2人（已更新报名说明）。"
            self.assertEqual(
                compute_job_fingerprint(initial_jobs[0]),
                compute_job_fingerprint(dup_job),
            )

            m2 = merge_jobs_into_dataset(
                [dup_job],
                root_dir=root,
                today=ref_today,
                archive_expired=False,
            )
            self.assertEqual(m2.inserted, 0)
            self.assertEqual(m2.updated, 1)
            self.assertEqual(m2.active_total, 3)

            jobs_after_m2 = json.loads((root / "data" / "jobs.json").read_text(encoding="utf-8"))["items"]
            # Original ID is preserved
            self.assertEqual(jobs_after_m2[0]["id"], "pub-henan-2026-1001")
            self.assertEqual(jobs_after_m2[0]["summary"], "省直全额拨款事业编2人（已更新报名说明）。")

            # 3. Archive expired jobs into data/archive/2026-10.json
            arch = archive_expired_jobs(root_dir=root, today=ref_today, remove_from_active=True)
            self.assertEqual(arch.archived_count, 1)
            self.assertEqual(arch.remaining_active, 2)
            self.assertEqual(arch.archive_files, ["data/archive/2026-10.json"])

            archive_file = root / "data" / "archive" / "2026-10.json"
            self.assertTrue(archive_file.exists())
            archived_data = json.loads(archive_file.read_text(encoding="utf-8"))
            self.assertEqual(len(archived_data["items"]), 1)
            self.assertEqual(archived_data["items"][0]["id"], "pub-henan-2026-0999")
            self.assertEqual(archived_data["items"][0]["status"], "expired")

    def test_base_adapter_end_to_end_with_mock_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CrawlStateStore(root_dir=root)

            routes: dict[str, tuple[str, bytes]] = {
                "https://hrss.henan.gov.cn/list.html": (
                    "text/html; charset=utf-8",
                    self.list_html_text.encode("utf-8"),
                ),
                "https://hrss.henan.gov.cn/2026/1118/announce_1001.html": (
                    "text/html; charset=utf-8",
                    self.html_utf8_text.encode("utf-8"),
                ),
                "https://hrss.henan.gov.cn/upload/202611/henan_2026_posts.xlsx": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    self.binary_paths["xlsx"].read_bytes(),
                ),
                "https://hrss.henan.gov.cn/2026/1118/attachments/henan_2026_guide.docx": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    self.binary_paths["docx"].read_bytes(),
                ),
                "https://hrss.henan.gov.cn/upload/202611/henan_2026_notice.pdf": (
                    "application/pdf",
                    self.binary_paths["pdf"].read_bytes(),
                ),
            }

            def handler(request: httpx.Request) -> httpx.Response:
                url = str(request.url)
                if url in routes:
                    ctype, body = routes[url]
                    return httpx.Response(200, headers={"Content-Type": ctype}, content=body)
                return httpx.Response(404, content=b"Not Found")

            src_cfg = {
                "source_id": "henan_hrss_gov",
                "name": "河南省人力资源和社会保障厅",
                "category": "public_institution",
                "province": "河南省",
                "city": "全省",
                "adapter": "henan_hrss",
                "base_url": "https://hrss.henan.gov.cn",
                "list_urls": ["https://hrss.henan.gov.cn/list.html"],
                "enabled": True,
                "url_verified": True,
            }

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(handler)) as client:
                adapter = HenanHrssAdapter(src_cfg, http_client=client, state_store=store)
                summary = adapter.run(dry_run=False, max_items=1)
                self.assertEqual(summary.list_urls_scanned, 1)
                self.assertEqual(summary.announcements_fetched, 1)
                self.assertEqual(summary.new_count, 1)
                self.assertEqual(len(summary.inbox_files), 1)

                inbox_files = list((root / "raw_inbox").glob("henan_hrss_gov_*.txt"))
                self.assertEqual(len(inbox_files), 1)
                inbox_text = inbox_files[0].read_text(encoding="utf-8")
                self.assertIn("省直岗位表", inbox_text)
                self.assertIn("报考人员须具备本科及以上学历", inbox_text)
                self.assertIn("职业能力测验", inbox_text)
                self.assertIn("[附件解析降级提示]", inbox_text)


if __name__ == "__main__":
    unittest.main()
