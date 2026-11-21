"""Offline unit tests for Henan provincial/municipal adapters and attachment discovery."""

from __future__ import annotations

import json
import tempfile
import unittest
import zlib
from pathlib import Path

import httpx

from scrapers.base import (
    ADAPTER_REGISTRY,
    BodaUniversityAdapter,
    EpointGovAdapter,
    HenanHaustRscAdapter,
    HenanHautRscAdapter,
    HenanHrssAdapter,
    HenanHrssYwzlAdapter,
    HenanJiyuanRsjAdapter,
    HenanLuoyangRsksAdapter,
    HenanPingdingshanRsjAdapter,
    HenanRsksAdapter,
    HenanSanmenxiaRsjAdapter,
    HenanShangqiuRsksAdapter,
    HenanXinxiangRsjAdapter,
    HenanXuchangRsjAdapter,
    HenanZhengzhouRsjAdapter,
    HenanZhoukouRsksAdapter,
    LongsiGovAdapter,
    TrsWcmGovAdapter,
    classify_announcement_title,
    get_adapter_class,
    is_recruitment_announcement,
)
from scrapers.core.extractors import (
    extract_attachment,
    extract_filename_from_content_disposition,
    extract_html,
    extract_pdf,
)
from scrapers.core.http_client import PoliteHttpClient
from scrapers.core.storage import CrawlStateStore
from tests.fixtures.fixture_builders import ensure_binary_fixtures

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _build_tounicode_pdf_bytes() -> bytes:
    """Build a minimal PDF with a ToUnicode CMap mapping <0001><0002> to '岗位'."""
    cmap = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\n"
        "begincmap\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        "2 beginbfchar\n<0001> <5C97>\n<0002> <4F4D>\nendbfchar\n"
        "endcmap\n"
    ).encode("ascii")
    cmap_comp = zlib.compress(cmap)
    content = b"BT /FT8 12 Tf <00010002> Tj ET"
    content_comp = zlib.compress(content)

    parts = [
        b"%PDF-1.4\n",
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /FT8 4 0 R >> >> /Contents 6 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type0 /Encoding /Identity-H /ToUnicode 5 0 R >> endobj\n",
        f"5 0 obj << /Length {len(cmap_comp)} /Filter /FlateDecode >>\nstream\n".encode("ascii"),
        cmap_comp,
        b"\nendstream\nendobj\n",
        f"6 0 obj << /Length {len(content_comp)} /Filter /FlateDecode >>\nstream\n".encode("ascii"),
        content_comp,
        b"\nendstream\nendobj\n",
        b"trailer << /Size 7 /Root 1 0 R >>\n%%EOF\n",
    ]
    return b"".join(parts)


def _build_unmapped_cid_pdf_bytes() -> bytes:
    """Build a CID Identity-H PDF lacking ToUnicode CMaps (would otherwise emit ASCII gibberish)."""
    content = zlib.compress(b"BT /FT8 12 Tf <4C7A2634665357> Tj ET")
    return b"".join([
        b"%PDF-1.4\n",
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /FT8 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type0 /Encoding /Identity-H /DescendantFonts [6 0 R] >> endobj\n",
        f"5 0 obj << /Length {len(content)} /Filter /FlateDecode >>\nstream\n".encode("ascii"),
        content,
        b"\nendstream\nendobj\n",
        b"6 0 obj << /Type /Font /Subtype /CIDFontType2 >> endobj\n",
        b"trailer << /Size 7 /Root 1 0 R >>\n%%EOF\n",
    ])


class HenanAdaptersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.binary_paths = ensure_binary_fixtures(FIXTURES_DIR)

    def test_adapter_registry_lookup(self) -> None:
        expected = {
            "henan_hrss": HenanHrssAdapter,
            "henan_hrss_ywzl": HenanHrssYwzlAdapter,
            "henan_rsks": HenanRsksAdapter,
            "henan_luoyang_rsks": HenanLuoyangRsksAdapter,
            "henan_xuchang_rsj": HenanXuchangRsjAdapter,
            "henan_zhoukou_rsks": HenanZhoukouRsksAdapter,
            "henan_zhengzhou_rsj": HenanZhengzhouRsjAdapter,
            "henan_pingdingshan_rsj": HenanPingdingshanRsjAdapter,
            "henan_xinxiang_rsj": HenanXinxiangRsjAdapter,
            "henan_sanmenxia_rsj": HenanSanmenxiaRsjAdapter,
            "henan_shangqiu_rsks": HenanShangqiuRsksAdapter,
            "henan_jiyuan_rsj": HenanJiyuanRsjAdapter,
            "henan_haut_rsc": HenanHautRscAdapter,
            "henan_haust_rsc": HenanHaustRscAdapter,
        }
        for key, cls in expected.items():
            self.assertIn(key, ADAPTER_REGISTRY)
            self.assertIs(get_adapter_class(key), cls)

        self.assertTrue(issubclass(HenanXuchangRsjAdapter, EpointGovAdapter))
        self.assertTrue(issubclass(HenanHautRscAdapter, BodaUniversityAdapter))
        self.assertTrue(issubclass(HenanHaustRscAdapter, BodaUniversityAdapter))
        self.assertTrue(issubclass(HenanXinxiangRsjAdapter, LongsiGovAdapter))
        self.assertTrue(issubclass(HenanJiyuanRsjAdapter, TrsWcmGovAdapter))

    def test_title_classification_and_filtering(self) -> None:
        self.assertEqual(
            classify_announcement_title("中共河南省委组织部 河南省人力资源和社会保障厅 2026年河南省事业单位公开招聘联考工作的公告"),
            "recruitment",
        )
        self.assertEqual(
            classify_announcement_title("许昌职业技术学院 2026年第二批考核招聘博士研究生公告"),
            "recruitment",
        )
        self.assertEqual(
            classify_announcement_title("2026年河南省（洛阳市）事业单位公开招聘联考面试成绩、总成绩及体检公告"),
            "post_exam_notice",
        )
        self.assertEqual(
            classify_announcement_title("河南省人民政府办公厅文印中心 2026年公开招聘体检公告"),
            "post_exam_notice",
        )
        self.assertEqual(
            classify_announcement_title("2026年河南省（商丘市）事业单位公开招聘联考拟聘用人员公示（第二批）"),
            "post_exam_notice",
        )
        self.assertEqual(
            classify_announcement_title("关于做好2026年度执业药师职业资格考试考务工作的通知"),
            "post_exam_notice",
        )
        self.assertEqual(
            classify_announcement_title("关于2026年郑州市事业单位公开招聘（市级联考）笔试最低合格分数线的公告"),
            "post_exam_notice",
        )
        self.assertEqual(
            classify_announcement_title("2024年博士研究生公开招聘第三批聘用人员名单公示"),
            "post_exam_notice",
        )
        self.assertFalse(
            is_recruitment_announcement("关于2026年郑州市事业单位公开招聘（市级联考）笔试最低合格分数线的公告")
        )
        self.assertFalse(
            is_recruitment_announcement("2024年博士研究生公开招聘第三批聘用人员名单公示")
        )
        self.assertEqual(
            classify_announcement_title("关于2026年度许昌市第一批一次性扩岗补助发放企业名单的公示"),
            "non_recruitment",
        )
        self.assertEqual(
            classify_announcement_title("西部某市公安局招聘辅警公告"),
            "non_recruitment",
        )
        self.assertFalse(is_recruitment_announcement("西部某市公安局招聘辅警公告"))
        self.assertFalse(is_recruitment_announcement("某市公安局公开招聘警务辅助人员公告"))
        self.assertFalse(is_recruitment_announcement("【就业专项活动】金秋揽才 筑梦济源 ——济源示范区2026年招才引智暨高校毕业生网..."))
        self.assertFalse(is_recruitment_announcement("2026年清明会招聘活动、送岗进社区招聘活动"))
        self.assertFalse(is_recruitment_announcement("2026年河南省新乡市事业单位公开招聘联考综合类、卫生类分组公告"))
        self.assertFalse(is_recruitment_announcement("河南省社会保险中心2026年信息化建设项目招标代理机构选聘竞争性磋商公告"))
        self.assertFalse(is_recruitment_announcement("关于商丘市2026年高校毕业生“三支一扶”计划招募笔试定补资金发放及补报银行卡信息的通知"))
        self.assertFalse(is_recruitment_announcement("2027年度宁夏招录选调生公告", allow_out_of_province=False))
        self.assertFalse(is_recruitment_announcement("北京市各级机关2027年度考试录用公务员公告", allow_out_of_province=False))
        self.assertTrue(is_recruitment_announcement("河南省2026年大学生乡村医生专项计划公开招聘公告", allow_out_of_province=False))

    def test_dynamic_attachment_discovery_and_content_disposition(self) -> None:
        html = """
        <html><body>
          <nav><a href="/download/guide.html">下载说明页面</a></nav>
          <div class="article-content">
            <p>测试动态附件：
              <a href="/api/appendix?id=101">附件1：2026年招聘岗位表</a>
              <a href="/cms/down?fileId=202">下载报名表</a>
            </p>
          </div>
        </body></html>
        """
        res = extract_html(html, base_url="https://hrss.henan.gov.cn/2026/news/1.html")
        att_urls = [a.url for a in res.attachments]
        self.assertIn("https://hrss.henan.gov.cn/api/appendix?id=101", att_urls)
        self.assertIn("https://hrss.henan.gov.cn/cms/down?fileId=202", att_urls)
        self.assertNotIn("https://hrss.henan.gov.cn/download/guide.html", att_urls)

        self.assertEqual(
            extract_filename_from_content_disposition('attachment;filename="2026岗位表.xlsx"'),
            "2026岗位表.xlsx",
        )
        self.assertEqual(
            extract_filename_from_content_disposition("attachment; filename*=UTF-8''%E5%B2%97%E4%BD%8D%E8%A1%A8.xlsx"),
            "岗位表.xlsx",
        )

        # Dynamic download endpoint without extension resolved via Content-Disposition
        xlsx_bytes = self.binary_paths["xlsx"].read_bytes()
        extracted = extract_attachment(
            xlsx_bytes,
            "http://www.zkrsks.com/zkrswz/public/fileDownloadServlet?id=9901",
            content_disposition="attachment; filename*=UTF-8''%E5%B2%97%E4%BD%8D%E8%A1%A8.xlsx",
            content_type="application/octet-stream",
        )
        self.assertTrue(extracted.supported)
        self.assertEqual(extracted.file_type, "xlsx")
        self.assertIn("省直岗位表", extracted.text)

    def test_pdf_tounicode_cmap_and_unmapped_cid_degradation(self) -> None:
        cmap_res = extract_pdf(_build_tounicode_pdf_bytes(), display_name="cmap.pdf")
        self.assertTrue(cmap_res.supported)
        self.assertIn("岗位", cmap_res.text)

        unmapped_res = extract_pdf(_build_unmapped_cid_pdf_bytes(), display_name="scan_or_cid.pdf")
        self.assertFalse(unmapped_res.supported)
        self.assertIn("附件解析降级提示", unmapped_res.text)
        self.assertNotIn("Lz", unmapped_res.text)

    def test_henan_rsks_adapter_offline_list_and_detail(self) -> None:
        list_html = (FIXTURES_DIR / "henan_rsks_list.html").read_text(encoding="utf-8")
        detail_html = (FIXTURES_DIR / "henan_rsks_detail.html").read_text(encoding="utf-8")

        routes = {
            "http://www.hnrsks.com/sitesources/hnsrskszx/page_pc/zngg/list1.html": (
                "text/html; charset=utf-8",
                list_html.encode("utf-8"),
                {},
            ),
            "http://www.hnrsks.com/sitesources/hnsrskszx/page_pc/zngg/article638db.html": (
                "text/html; charset=utf-8",
                detail_html.encode("utf-8"),
                {},
            ),
            "http://www.hnrsks.com/sitesources/hnsrskszx/upload/202604/henan_2026_posts.xlsx": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                self.binary_paths["xlsx"].read_bytes(),
                {},
            ),
            "http://www.hnrsks.com/sitesources/hnsrskszx/upload/202604/henan_2026_guide.docx": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                self.binary_paths["docx"].read_bytes(),
                {},
            ),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            u = str(request.url)
            if u in routes:
                ctype, body, hdrs = routes[u]
                return httpx.Response(200, headers={"Content-Type": ctype, **hdrs}, content=body)
            return httpx.Response(404, content=b"Not Found")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CrawlStateStore(root_dir=root)
            cfg = {
                "source_id": "henan_rsks",
                "name": "河南省人事考试中心",
                "base_url": "http://www.hnrsks.com",
                "list_urls": ["http://www.hnrsks.com/sitesources/hnsrskszx/page_pc/zngg/list1.html"],
            }
            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(handler)) as client:
                adapter = HenanRsksAdapter(cfg, http_client=client, state_store=store)
                stubs = adapter.parse_list_page(list_html, cfg["list_urls"][0])
                self.assertEqual(len(stubs), 1)
                self.assertIn("2026年河南省事业单位公开招聘联考工作的公告", stubs[0].title)
                self.assertEqual(stubs[0].published_at, "2026-04-22")

                summary = adapter.run(dry_run=False, max_items=5)
                self.assertEqual(summary.new_count, 1)
                self.assertEqual(summary.errors, [])

                inbox_txt = list((root / "raw_inbox").glob("henan_rsks_*.txt"))[0].read_text(encoding="utf-8")
                self.assertNotIn("<br>", inbox_txt)
                self.assertNotIn("file:///C:/", inbox_txt)
                self.assertNotIn("版权所有", inbox_txt)
                self.assertIn("省直岗位表", inbox_txt)
                self.assertIn("报考指南", inbox_txt)

                # Incremental dedup run
                summary2 = adapter.run(dry_run=False, max_items=5)
                self.assertEqual(summary2.new_count, 0)
                self.assertEqual(summary2.unchanged_count, 1)

    def test_luoyang_xuchang_and_zhoukou_adapters_offline(self) -> None:
        ly_list = (FIXTURES_DIR / "henan_luoyang_list_reprint.html").read_text(encoding="utf-8")
        ly_detail = (FIXTURES_DIR / "henan_luoyang_detail.html").read_text(encoding="utf-8")
        xc_list = (FIXTURES_DIR / "henan_xuchang_list.html").read_text(encoding="utf-8")
        xc_detail = (FIXTURES_DIR / "henan_xuchang_detail.html").read_text(encoding="utf-8")
        zk_list = (FIXTURES_DIR / "henan_zhoukou_list.json").read_text(encoding="utf-8")
        zk_detail = (FIXTURES_DIR / "henan_zhoukou_detail.json").read_text(encoding="utf-8")

        xlsx_bytes = self.binary_paths["xlsx"].read_bytes()
        docx_bytes = self.binary_paths["docx"].read_bytes()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CrawlStateStore(root_dir=root)

            # 1. Luoyang reprint filter + download attribute
            ly_cfg = {
                "source_id": "henan_luoyang_rsks",
                "name": "洛阳人事考试网",
                "base_url": "https://www.lysrsks.cn",
                "list_urls": ["https://www.lysrsks.cn/index/news/index.html?classfiy=45"],
            }
            ly_routes = {
                "https://www.lysrsks.cn/index/news/index.html?classfiy=45": ("text/html; charset=utf-8", ly_list.encode("utf-8"), {}),
                "https://www.lysrsks.cn/index/news/detail.html?id=2641": ("text/html; charset=utf-8", ly_detail.encode("utf-8"), {}),
                "https://xcoss.henan.gov.cn/typtfile/20261028/posts.xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_bytes, {}),
            }

            def ly_handler(req: httpx.Request) -> httpx.Response:
                u = str(req.url)
                if u in ly_routes:
                    ct, b, h = ly_routes[u]
                    return httpx.Response(200, headers={"Content-Type": ct, **h}, content=b)
                return httpx.Response(404)

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(ly_handler)) as c:
                ly_adapter = HenanLuoyangRsksAdapter(ly_cfg, http_client=c, state_store=store)
                ly_stubs = ly_adapter.parse_list_page(ly_list, ly_cfg["list_urls"][0])
                self.assertEqual(len(ly_stubs), 1)
                self.assertEqual(ly_stubs[0].title, "河南省2026年大学生乡村医生专项计划公开招聘公告")
                ly_res = ly_adapter.process_detail_page(ly_stubs[0], fetch_attachments=True)
                self.assertEqual(ly_res.status, "new")
                assert ly_res.inbox_path is not None
                ly_txt = ly_res.inbox_path.read_text(encoding="utf-8")
                self.assertNotIn("左侧导航栏噪音", ly_txt)
                self.assertIn("type=doc", ly_txt)
                self.assertIn("(格式: doc)", ly_txt)
                self.assertIn("河南省大学生乡村医生公开招聘报名登记表", ly_txt)

            # 2. Xuchang list (pagination ignored) + detail
            xc_cfg = {
                "source_id": "henan_xuchang_rsj",
                "name": "许昌市人社局",
                "base_url": "https://rsj.xuchang.gov.cn",
                "list_urls": ["https://rsj.xuchang.gov.cn/zxdt/002002/about.html"],
            }
            xc_adapter = HenanXuchangRsjAdapter(
                xc_cfg,
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=xc_detail.encode("utf-8")))),
                state_store=store,
            )
            xc_stubs = xc_adapter.parse_list_page(xc_list, xc_cfg["list_urls"][0])
            self.assertEqual(len(xc_stubs), 1)
            self.assertEqual(xc_stubs[0].title, "许昌职业技术学院 2026年第二批考核招聘博士研究生公告")
            self.assertEqual(xc_stubs[0].published_at, "2026-11-04")

            # 3. Zhoukou JSON API + fileDownloadServlet + Content-Disposition
            zk_cfg = {
                "source_id": "henan_zhoukou_rsks",
                "name": "周口人事考试网",
                "base_url": "http://www.zkrsks.com",
                "list_urls": [
                    "http://www.zkrsks.com/zkrswz/zkrsksw/website/exam/getProfessionExamList?resourceClassify=3109&resourceItem=310902&pageSize=16&pageNum=1&systemClassify=3"
                ],
            }

            def zk_handler(req: httpx.Request) -> httpx.Response:
                u = str(req.url)
                if "getProfessionExamList" in u:
                    return httpx.Response(200, headers={"Content-Type": "application/json"}, content=zk_list.encode("utf-8"))
                if "getContentInfo" in u:
                    return httpx.Response(200, headers={"Content-Type": "application/json"}, content=zk_detail.encode("utf-8"))
                if "id=9901" in u:
                    return httpx.Response(
                        200,
                        headers={
                            "Content-Type": "application/octet-stream",
                            "Content-Disposition": "attachment; filename*=UTF-8''%E5%B2%97%E4%BD%8D%E4%BF%A1%E6%81%AF%E8%A1%A8.xlsx",
                        },
                        content=xlsx_bytes,
                    )
                if "guide.docx" in u:
                    return httpx.Response(
                        200,
                        headers={
                            "Content-Type": "application/octet-stream",
                            "Content-Disposition": "attachment;filename=guide.docx",
                        },
                        content=docx_bytes,
                    )
                return httpx.Response(404)

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(zk_handler)) as c:
                zk_adapter = HenanZhoukouRsksAdapter(zk_cfg, http_client=c, state_store=store)
                zk_summary = zk_adapter.run(dry_run=False, max_items=5)
                self.assertEqual(zk_summary.new_count, 1)
                self.assertEqual(zk_summary.errors, [])

                zk_file = list((root / "raw_inbox").glob("henan_zhoukou_rsks_*.txt"))[0]
                zk_txt = zk_file.read_text(encoding="utf-8")
                self.assertIn("source_url: http://www.zkrsks.com/new.html?contentNum=29ae2c6f02c14761b78fc1efe6a3b188", zk_txt)
                self.assertIn("type=xlsx", zk_txt)
                self.assertIn("省直岗位表", zk_txt)
                self.assertIn("笔试考试大纲.docx", zk_txt)
                self.assertNotIn("old.xls", zk_txt)

    def test_new_henan_adapters_offline(self) -> None:
        ywzl_list = (FIXTURES_DIR / "henan_ywzl_list.html").read_text(encoding="utf-8")
        ywzl_detail = (FIXTURES_DIR / "henan_ywzl_detail.html").read_text(encoding="utf-8")
        zz_list = (FIXTURES_DIR / "henan_zhengzhou_list.html").read_text(encoding="utf-8")
        zz_detail = (FIXTURES_DIR / "henan_zhengzhou_detail_public.html").read_text(encoding="utf-8")
        pds_list = (FIXTURES_DIR / "henan_pingdingshan_list.html").read_text(encoding="utf-8")
        haut_list = (FIXTURES_DIR / "henan_haut_list.html").read_text(encoding="utf-8")
        haust_list = (FIXTURES_DIR / "henan_haust_list.html").read_text(encoding="utf-8")

        xlsx_bytes = self.binary_paths["xlsx"].read_bytes()
        docx_bytes = self.binary_paths["docx"].read_bytes()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CrawlStateStore(root_dir=root)

            # 1. Henan HRSS Business Column (ywzl.hrss.henan.gov.cn)
            ywzl_cfg = {
                "source_id": "henan_hrss_ywzl",
                "name": "河南省人社厅业务专栏",
                "base_url": "https://ywzl.hrss.henan.gov.cn",
                "list_urls": ["https://ywzl.hrss.henan.gov.cn/viewCmsCac.do?cacId=4aef1408279926e601279e53eac517a5"],
            }
            ywzl_routes = {
                ywzl_cfg["list_urls"][0]: ("text/html; charset=utf-8", ywzl_list.encode("utf-8")),
                "https://ywzl.hrss.henan.gov.cn/sitegroup/root/html/4aef1408279926e601279e53eac517a5/72c3f5b1d0ef4a158eaf2ce161846445.html": (
                    "text/html; charset=utf-8",
                    ywzl_detail.encode("utf-8"),
                ),
                "https://ywzl.hrss.henan.gov.cn/ueditor/jsp/upload/file/20261111/1794387664128067705.docx": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    docx_bytes,
                ),
            }

            def ywzl_handler(req: httpx.Request) -> httpx.Response:
                u = str(req.url)
                if u in ywzl_routes:
                    ct, b = ywzl_routes[u]
                    return httpx.Response(200, headers={"Content-Type": ct}, content=b)
                return httpx.Response(404)

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(ywzl_handler)) as c:
                ywzl_adapter = HenanHrssYwzlAdapter(ywzl_cfg, http_client=c, state_store=store)
                ywzl_stubs = ywzl_adapter.parse_list_page(ywzl_list, ywzl_cfg["list_urls"][0])
                self.assertEqual(len(ywzl_stubs), 2)
                self.assertEqual(ywzl_stubs[0].published_at, "2026-11-13")
                self.assertEqual(ywzl_stubs[1].published_at, "2026-11-11")
                res = ywzl_adapter.process_detail_page(ywzl_stubs[1], fetch_attachments=True)
                self.assertEqual(res.status, "new")
                assert res.inbox_path is not None
                txt = res.inbox_path.read_text(encoding="utf-8")
                self.assertIn("title: 河南省地质局所属事业单位2026年公开招聘高层次人才公告", txt)
                self.assertIn("报考人员须具备本科及以上学历", txt)

            # 2. Zhengzhou HRSS (multi-host zzrs.zhengzhou.gov.cn + public.zhengzhou.gov.cn)
            zz_cfg = {
                "source_id": "henan_zhengzhou_rsj",
                "name": "郑州市人社局",
                "base_url": "https://zzrs.zhengzhou.gov.cn",
                "list_urls": ["https://zzrs.zhengzhou.gov.cn/zlgg/index.jhtml"],
            }
            zz_routes = {
                "https://zzrs.zhengzhou.gov.cn/zlgg/index.jhtml": ("text/html; charset=utf-8", zz_list.encode("utf-8")),
                "https://public.zhengzhou.gov.cn/D1703X/10149300.jhtml": ("text/html; charset=utf-8", zz_detail.encode("utf-8")),
                "https://public.zhengzhou.gov.cn/upload/posts.docx": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    docx_bytes,
                ),
            }

            def zz_handler(req: httpx.Request) -> httpx.Response:
                u = str(req.url)
                if u in zz_routes:
                    ct, b = zz_routes[u]
                    return httpx.Response(200, headers={"Content-Type": ct}, content=b)
                return httpx.Response(404)

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(zz_handler)) as c:
                zz_adapter = HenanZhengzhouRsjAdapter(zz_cfg, http_client=c, state_store=store)
                zz_stubs = zz_adapter.parse_list_page(zz_list, zz_cfg["list_urls"][0])
                self.assertEqual(len(zz_stubs), 2)
                self.assertEqual(zz_stubs[0].title, "郑州旅游职业学院2026年公开招聘工作人员（博士）公告")
                self.assertEqual(zz_stubs[0].published_at, "2026-07-10")
                self.assertEqual(zz_stubs[1].url, "https://zzrs.zhengzhou.gov.cn/zlgg/10124375.jhtml")
                zz_res = zz_adapter.process_detail_page(zz_stubs[0], fetch_attachments=True)
                self.assertEqual(zz_res.status, "new")
                assert zz_res.inbox_path is not None
                zz_txt = zz_res.inbox_path.read_text(encoding="utf-8")
                self.assertIn("title: 郑州旅游职业学院2026年公开招聘工作人员（博士）公告", zz_txt)
                self.assertIn("郑州旅游职业学院岗位信息表.docx", zz_txt)

            # 3. Pingdingshan HRSS (ul.channel-list)
            pds_cfg = {
                "source_id": "henan_pingdingshan_rsj",
                "name": "平顶山市人社局",
                "base_url": "https://rsj.pds.gov.cn",
                "list_urls": ["https://rsj.pds.gov.cn/channels/13460.html"],
            }
            pds_adapter = HenanPingdingshanRsjAdapter(
                pds_cfg,
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=xlsx_bytes))),
                state_store=store,
            )
            pds_stubs = pds_adapter.parse_list_page(pds_list, pds_cfg["list_urls"][0])
            self.assertEqual(len(pds_stubs), 1)
            self.assertEqual(pds_stubs[0].title, "尧山实验室2026年度高层次人才公开招聘方案")
            self.assertEqual(pds_stubs[0].published_at, "2026-06-23")

            # 4. HAUT & HAUST university HR adapters
            haut_cfg = {
                "source_id": "henan_haut_rsc",
                "name": "河南工业大学人事处",
                "base_url": "https://rsc.haut.edu.cn",
                "list_urls": ["https://rsc.haut.edu.cn/rczp.htm"],
            }
            haut_adapter = HenanHautRscAdapter(
                haut_cfg,
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda r: httpx.Response(404))),
                state_store=store,
            )
            haut_stubs = haut_adapter.parse_list_page(haut_list, haut_cfg["list_urls"][0])
            self.assertEqual(len(haut_stubs), 1)
            self.assertEqual(haut_stubs[0].title, "河南工业大学2026年公开招聘工作人员公告")
            self.assertEqual(haut_stubs[0].published_at, "2026-11-11")

            haust_cfg = {
                "source_id": "henan_haust_rsc",
                "name": "河南科技大学人事处",
                "base_url": "https://rsc.haust.edu.cn",
                "list_urls": ["https://rsc.haust.edu.cn/rczp/zpgg.htm"],
            }
            haust_adapter = HenanHaustRscAdapter(
                haust_cfg,
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda r: httpx.Response(404))),
                state_store=store,
            )
            haust_stubs = haust_adapter.parse_list_page(haust_list, haust_cfg["list_urls"][0])
            self.assertEqual(len(haust_stubs), 1)
            self.assertEqual(haust_stubs[0].title, "河南科技大学2026年度博士招聘公告")
            self.assertEqual(haust_stubs[0].published_at, "2026-09-30")

    def test_campaign_5b_municipal_adapters_and_cms_bases_offline(self) -> None:
        xx_list = (FIXTURES_DIR / "henan_xinxiang_list.html").read_text(encoding="utf-8")
        xx_detail = (FIXTURES_DIR / "henan_xinxiang_detail.html").read_text(encoding="utf-8")
        smx_list = (FIXTURES_DIR / "henan_sanmenxia_list.html").read_text(encoding="utf-8")
        smx_detail = (FIXTURES_DIR / "henan_sanmenxia_detail.html").read_text(encoding="utf-8")
        sq_list = (FIXTURES_DIR / "henan_shangqiu_list.html").read_text(encoding="utf-8")
        sq_detail = (FIXTURES_DIR / "henan_shangqiu_detail.html").read_text(encoding="utf-8")
        jy_tzgg = (FIXTURES_DIR / "henan_jiyuan_list_tzgg.html").read_text(encoding="utf-8")
        jy_rsks = (FIXTURES_DIR / "henan_jiyuan_list_rsks.html").read_text(encoding="utf-8")
        jy_detail = (FIXTURES_DIR / "henan_jiyuan_detail.html").read_text(encoding="utf-8")

        xlsx_bytes = self.binary_paths["xlsx"].read_bytes()
        docx_bytes = self.binary_paths["docx"].read_bytes()
        pdf_bytes = _build_tounicode_pdf_bytes()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CrawlStateStore(root_dir=root)

            # 1. Xinxiang (LongsiGovAdapter)
            xx_cfg = {
                "source_id": "henan_xinxiang_rsj",
                "name": "新乡市人社局",
                "base_url": "https://hrss.xinxiang.gov.cn",
                "list_urls": ["https://hrss.xinxiang.gov.cn/xwzx/tzgg/index.html"],
            }
            xx_routes = {
                "https://hrss.xinxiang.gov.cn/xwzx/tzgg/index.html": ("text/html; charset=utf-8", xx_list.encode("utf-8")),
                "https://hrss.xinxiang.gov.cn/xwzx/tzgg/1297375.html": ("text/html; charset=utf-8", xx_detail.encode("utf-8")),
                "https://hrss.xinxiang.gov.cn/group3/M00/00/D5/posts.xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_bytes),
                "https://hrss.xinxiang.gov.cn/group3/M00/00/D5/guide.docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", docx_bytes),
            }

            def xx_handler(req: httpx.Request) -> httpx.Response:
                u = str(req.url)
                if u in xx_routes:
                    ct, b = xx_routes[u]
                    return httpx.Response(200, headers={"Content-Type": ct}, content=b)
                return httpx.Response(404)

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(xx_handler)) as c:
                xx_adapter = HenanXinxiangRsjAdapter(xx_cfg, http_client=c, state_store=store)
                xx_stubs = xx_adapter.parse_list_page(xx_list, xx_cfg["list_urls"][0])
                self.assertEqual(len(xx_stubs), 2)
                self.assertEqual(xx_stubs[0].title, "河南省2026年高校毕业生“三支一扶”计划招募公告")
                self.assertEqual(xx_stubs[0].published_at, "2026-08-08")
                self.assertEqual(xx_stubs[1].title, "2026年河南省新乡市事业单位公开招聘联考工作的公告")
                xx_res = xx_adapter.process_detail_page(xx_stubs[0], fetch_attachments=True)
                self.assertEqual(xx_res.status, "new")
                assert xx_res.inbox_path is not None
                xx_txt = xx_res.inbox_path.read_text(encoding="utf-8")
                self.assertIn("省直岗位表", xx_txt)
                self.assertIn("报考指南", xx_txt)

            # 2. Sanmenxia (host filter + badge strip + "首页轮播" title guard)
            smx_cfg = {
                "source_id": "henan_sanmenxia_rsj",
                "name": "三门峡市人社局",
                "base_url": "https://rsj.smx.gov.cn",
                "list_urls": ["https://rsj.smx.gov.cn/600/0000/zhengfuxinxi-1.html"],
            }
            smx_routes = {
                "https://rsj.smx.gov.cn/600/0000/zhengfuxinxi-1.html": ("text/html; charset=utf-8", smx_list.encode("utf-8")),
                "http://rsj.smx.gov.cn/600/2026/4/2250752.html": ("text/html; charset=utf-8", smx_detail.encode("utf-8")),
                "https://minio.smxzhcs.cn/smxol-pre/posts.xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_bytes),
                "https://minio.smxzhcs.cn/smxol-pre/guide.docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", docx_bytes),
            }

            def smx_handler(req: httpx.Request) -> httpx.Response:
                u = str(req.url)
                if u in smx_routes:
                    ct, b = smx_routes[u]
                    return httpx.Response(200, headers={"Content-Type": ct}, content=b)
                return httpx.Response(404)

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(smx_handler)) as c:
                smx_adapter = HenanSanmenxiaRsjAdapter(smx_cfg, http_client=c, state_store=store)
                smx_stubs = smx_adapter.parse_list_page(smx_list, smx_cfg["list_urls"][0])
                self.assertEqual(len(smx_stubs), 1)
                self.assertEqual(
                    smx_stubs[0].title,
                    "中共三门峡市委组织部 三门峡市人力资源和社会保障局2026年河南省（三门峡市）事业单位公开招聘联考工作公告",
                )
                self.assertEqual(smx_stubs[0].published_at, "2026-04-22")
                smx_res = smx_adapter.process_detail_page(smx_stubs[0], fetch_attachments=True)
                self.assertEqual(smx_res.status, "new")
                assert smx_res.inbox_path is not None
                smx_txt = smx_res.inbox_path.read_text(encoding="utf-8")
                self.assertNotIn("首页轮播", smx_txt)
                self.assertIn("学历要求专科、本科及硕士", smx_txt)
                self.assertIn("省直岗位表", smx_txt)

            # 3. Shangqiu (query-based detail_url_pattern)
            sq_cfg = {
                "source_id": "henan_shangqiu_rsks",
                "name": "商丘市人事考试中心",
                "base_url": "http://www.sqrsks.cn",
                "list_urls": ["http://www.sqrsks.cn/index.php?s=index/category/index&id=22"],
            }
            sq_routes = {
                "http://www.sqrsks.cn/index.php?s=index/category/index&id=22": ("text/html; charset=utf-8", sq_list.encode("utf-8")),
                "http://www.sqrsks.cn/index.php?s=index/show/index&id=1337": ("text/html; charset=utf-8", sq_detail.encode("utf-8")),
                "http://www.sqrsks.cn/uploads/file/20260423/posts.xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_bytes),
                "http://www.sqrsks.cn/uploads/file/20260422/guide.docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", docx_bytes),
            }

            def sq_handler(req: httpx.Request) -> httpx.Response:
                u = str(req.url)
                if u in sq_routes:
                    ct, b = sq_routes[u]
                    return httpx.Response(200, headers={"Content-Type": ct}, content=b)
                return httpx.Response(404)

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(sq_handler)) as c:
                sq_adapter = HenanShangqiuRsksAdapter(sq_cfg, http_client=c, state_store=store)
                sq_stubs = sq_adapter.parse_list_page(sq_list, sq_cfg["list_urls"][0])
                self.assertEqual(len(sq_stubs), 2)
                self.assertEqual(sq_stubs[0].title, "关于组织实施2026年乡村振兴村级协理员专项计划的公告")
                self.assertEqual(sq_stubs[1].title, "2026年河南省（商丘市）事业单位公开招聘联考公告")
                sq_res = sq_adapter.process_detail_page(sq_stubs[1], fetch_attachments=True)
                self.assertEqual(sq_res.status, "new")
                assert sq_res.inbox_path is not None
                sq_txt = sq_res.inbox_path.read_text(encoding="utf-8")
                self.assertIn("title: 2026年河南省（商丘市）事业单位公开招聘联考公告", sq_txt)
                self.assertIn("省直岗位表", sq_txt)

            # 4. Jiyuan (TrsWcmGovAdapter: 2-digit list date + dual list templates)
            jy_cfg = {
                "source_id": "henan_jiyuan_rsj",
                "name": "济源示范区人社局",
                "base_url": "https://rbj.jiyuan.gov.cn",
                "list_urls": [
                    "https://rbj.jiyuan.gov.cn/rbj_xwzx/rbj_tzgg/",
                    "https://rbj.jiyuan.gov.cn/rsks/gggs_ks/",
                ],
            }
            jy_routes = {
                "https://rbj.jiyuan.gov.cn/rbj_xwzx/rbj_tzgg/": ("text/html; charset=utf-8", jy_tzgg.encode("utf-8")),
                "https://rbj.jiyuan.gov.cn/rsks/gggs_ks/": ("text/html; charset=utf-8", jy_rsks.encode("utf-8")),
                "https://rbj.jiyuan.gov.cn/rbj_xwzx/rbj_tzgg/t1012339.html": ("text/html; charset=utf-8", jy_detail.encode("utf-8")),
                "https://rbj.jiyuan.gov.cn/rbj_xwzx/rbj_tzgg/P020261109.pdf": ("application/pdf", pdf_bytes),
            }

            def jy_handler(req: httpx.Request) -> httpx.Response:
                u = str(req.url)
                if u in jy_routes:
                    ct, b = jy_routes[u]
                    return httpx.Response(200, headers={"Content-Type": ct}, content=b)
                return httpx.Response(404)

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(jy_handler)) as c:
                jy_adapter = HenanJiyuanRsjAdapter(jy_cfg, http_client=c, state_store=store)
                tzgg_stubs = jy_adapter.parse_list_page(jy_tzgg, jy_cfg["list_urls"][0])
                self.assertEqual(len(tzgg_stubs), 2)
                self.assertEqual(tzgg_stubs[0].title, "济源示范区招聘2027届国家公费师范毕业生公告")
                self.assertEqual(tzgg_stubs[0].published_at, "2026-11-09")
                self.assertEqual(tzgg_stubs[1].title, "济源产城融合示范区 2026年第四批公益性岗位招聘公告")
                self.assertEqual(tzgg_stubs[1].published_at, "2026-10-26")

                rsks_stubs = jy_adapter.parse_list_page(jy_rsks, jy_cfg["list_urls"][1])
                self.assertEqual(rsks_stubs, [])

                jy_res = jy_adapter.process_detail_page(tzgg_stubs[0], fetch_attachments=True)
                self.assertEqual(jy_res.status, "new")
                assert jy_res.inbox_path is not None
                jy_txt = jy_res.inbox_path.read_text(encoding="utf-8")
                self.assertIn("title: 济源示范区招聘2027届国家公费师范毕业生公告", jy_txt)
                self.assertIn("2027届公费师范生岗位需求表.pdf", jy_txt)
                self.assertIn("岗位", jy_txt)


if __name__ == "__main__":
    unittest.main()
