"""Offline unit tests for scripts/daily_sync.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts.daily_sync import format_sync_report, list_pending_inbox_files, run_daily_sync
from scrapers.core.http_client import PoliteHttpClient

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class DailySyncTests(unittest.TestCase):
    def _setup_temp_workspace(self, root: Path) -> None:
        _write_json(
            root / "config" / "sources.json",
            {
                "schema_version": "1.0",
                "sources": [
                    {
                        "source_id": "henan_rsks",
                        "name": "河南省人事考试中心",
                        "category": "public_institution",
                        "province": "河南省",
                        "city": "全省",
                        "adapter": "henan_rsks",
                        "base_url": "http://www.hnrsks.com",
                        "list_urls": ["http://www.hnrsks.com/sitesources/hnsrskszx/page_pc/zngg/list1.html"],
                        "enabled": True,
                        "url_verified": True,
                    },
                    {
                        "source_id": "ent_dify_jobs",
                        "name": "苏州语灵人工智能科技有限公司（Dify）",
                        "category": "enterprise",
                        "province": "江苏省",
                        "city": "多城市",
                        "adapter": "dify_jobs",
                        "base_url": "https://join.dify.ai",
                        "list_urls": ["https://qcnurokxgtyuimuixztr.supabase.co/functions/v1/public-jobs?lang=zh"],
                        "enabled": True,
                        "url_verified": True,
                    },
                    {
                        "source_id": "henan_disabled",
                        "name": "未启用测试源",
                        "category": "public_institution",
                        "province": "河南省",
                        "city": "全省",
                        "adapter": "henan_hrss",
                        "base_url": "https://hrss.henan.gov.cn",
                        "list_urls": ["https://hrss.henan.gov.cn/disabled.html"],
                        "enabled": False,
                        "url_verified": True,
                    },
                ],
            },
        )
        _write_json(
            root / "data" / "companies.json",
            {
                "schema_version": "1.0",
                "updated_at": "2026-11-18T12:00:00+08:00",
                "items": [
                    {
                        "company_id": "dify-langgenius",
                        "name": "苏州语灵人工智能科技有限公司（Dify）",
                        "industry": "AI / 开源平台",
                        "cities": ["苏州市", "远程"],
                        "career_url": "https://join.dify.ai/roles",
                        "saas_platform": "custom",
                        "work_schedule": "弹性工作",
                        "benefit_claims": [
                            {
                                "label": "周末双休",
                                "evidence_quote": "我们实行周末双休及灵活远程办公制度。",
                                "evidence_url": "https://join.dify.ai/roles",
                                "verified_at": "2026-11-18",
                            }
                        ],
                        "status": "active",
                    }
                ],
            },
        )
        # Seed 1 already-expired public job to verify auto-expire & monthly archive
        _write_json(
            root / "data" / "jobs.json",
            {
                "schema_version": "1.0",
                "updated_at": "2026-11-18T12:00:00+08:00",
                "items": [
                    {
                        "id": "pub-old-expired-01",
                        "category": "public_institution",
                        "title": "历史测试岗位（应被归档）",
                        "org_name": "测试事业单位",
                        "province": "河南省",
                        "city": "郑州市",
                        "source_url": "http://www.hnrsks.com/old.html",
                        "source_name": "河南省人事考试中心",
                        "source_type": "gov_official",
                        "published_at": "2026-10-01",
                        "deadline_at": "2026-10-15",
                        "status": "active",
                        "scraped_at": "2026-10-01T10:00:00+08:00",
                        "last_seen_at": "2026-10-01T10:00:00+08:00",
                        "content_hash": "1234567890abcdef",
                        "summary": "2026-10 已截止岗位测试。",
                        "tags": ["测试"],
                        "public_meta": {
                            "bianzhi_type": "全额拨款事业编",
                            "education_min": "本科",
                            "majors": ["不限"],
                            "attachments": [],
                        },
                    }
                ],
            },
        )
        (root / "data" / "archive").mkdir(parents=True, exist_ok=True)
        (root / "raw_inbox").mkdir(parents=True, exist_ok=True)

    def _mock_transport(self) -> httpx.MockTransport:
        rsks_list = (FIXTURES_DIR / "henan_rsks_list.html").read_text(encoding="utf-8")
        rsks_detail = (FIXTURES_DIR / "henan_rsks_detail.html").read_text(encoding="utf-8")
        dify_json = (FIXTURES_DIR / "saas_dify_jobs.json").read_text(encoding="utf-8")

        def handler(req: httpx.Request) -> httpx.Response:
            u = str(req.url)
            if "zngg/list1.html" in u:
                return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, content=rsks_list.encode("utf-8"))
            if "article638db.html" in u:
                return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, content=rsks_detail.encode("utf-8"))
            if "public-jobs" in u:
                return httpx.Response(200, headers={"Content-Type": "application/json"}, content=dify_json.encode("utf-8"))
            return httpx.Response(404, content=b"Not Found")

        return httpx.MockTransport(handler)

    def test_daily_sync_dry_run_and_live_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._setup_temp_workspace(root)
            transport = self._mock_transport()

            def client_factory(*args, **kwargs) -> PoliteHttpClient:
                kwargs["transport"] = transport
                kwargs["min_interval_sec"] = 0.0
                return PoliteHttpClient(*args, **kwargs)

            with patch("scrapers.run.PoliteHttpClient", side_effect=client_factory):
                dry_report = run_daily_sync(
                    root_dir=root,
                    dry_run=True,
                    max_items=5,
                    min_interval_sec=0.0,
                    today=date(2026, 11, 19),
                )
                self.assertEqual(dry_report.validation_errors, [])
                self.assertEqual(len(dry_report.scrape_summaries), 2)
                self.assertGreater(dry_report.newly_inserted_jobs, 0)
                self.assertIsNotNone(dry_report.archive_summary)
                assert dry_report.archive_summary is not None
                self.assertEqual(dry_report.archive_summary.archived_count, 1)
                self.assertEqual(list_pending_inbox_files(root), [])
                self.assertFalse((root / "data" / "archive" / "2026-10.json").exists())

                live_report = run_daily_sync(
                    root_dir=root,
                    dry_run=False,
                    max_items=5,
                    min_interval_sec=0.0,
                    today=date(2026, 11, 19),
                )
                self.assertEqual(live_report.validation_errors, [])
                self.assertGreater(live_report.newly_inserted_jobs, 0)
                self.assertEqual(live_report.jobs_after_archive, live_report.newly_inserted_jobs)
                self.assertTrue((root / "data" / "archive" / "2026-10.json").exists())
                self.assertEqual(len(live_report.pending_inbox_files), 1)
                self.assertIn("2026年河南省事业单位公开招聘联考工作的公告", live_report.pending_inbox_files[0].title)

                text_out = format_sync_report(live_report)
                self.assertIn("本次新增入库岗位数", text_out)
                self.assertIn("raw_inbox/ 待人工或AI解析文件清单", text_out)
                self.assertIn("[OK] Validation passed", text_out)


if __name__ == "__main__":
    unittest.main()
