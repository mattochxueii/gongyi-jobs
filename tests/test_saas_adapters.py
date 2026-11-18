"""Offline unit tests for standardized SaaS & official recruitment JSON adapters."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from scrapers.adapters.saas import normalize_cn_city, parse_date_value
from scrapers.base import (
    ADAPTER_REGISTRY,
    DifyJobsAdapter,
    EmqJobsAdapter,
    FeishuHireAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    MokaAdapter,
    get_adapter_class,
)
from scrapers.core.http_client import PoliteHttpClient
from scrapers.core.storage import CrawlStateStore
from scripts.validate_data import validate_workspace

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _init_temp_workspace(root: Path, company: dict) -> CrawlStateStore:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "config" / "sources.json").write_text(
        json.dumps({"schema_version": "1.0", "sources": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "data" / "companies.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "updated_at": "2026-11-18T17:00:00+08:00",
                "items": [company],
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
                "updated_at": "2026-11-18T17:00:00+08:00",
                "items": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return CrawlStateStore(root_dir=root)


class SaasAdaptersTests(unittest.TestCase):
    def test_saas_registry_lookup(self) -> None:
        expected = {
            "feishu_hire": FeishuHireAdapter,
            "greenhouse": GreenhouseAdapter,
            "lever": LeverAdapter,
            "moka": MokaAdapter,
            "emq_jobs": EmqJobsAdapter,
            "dify_jobs": DifyJobsAdapter,
        }
        for name, cls in expected.items():
            self.assertIn(name, ADAPTER_REGISTRY)
            self.assertIs(get_adapter_class(name), cls)

    def test_city_normalization_and_date_parsing(self) -> None:
        # GB city_id takes priority over street-name substrings ("北京路" in Guangzhou)
        self.assertEqual(normalize_cn_city("北京路商业大厦", city_id=440103), ("广东省", "广州市"))
        self.assertEqual(normalize_cn_city("郑州, 深圳"), ("河南省", "郑州市"))
        self.assertEqual(normalize_cn_city("Home Based - APAC"), ("全国/远程", "远程"))
        self.assertEqual(parse_date_value("2026-11-11T06:46:53-05:00"), "2026-11-11")
        self.assertEqual(parse_date_value(1794186712892), "2026-11-09")

    def test_feishu_hire_adapter_offline_and_idempotent_merge(self) -> None:
        portal_html = (FIXTURES_DIR / "saas_feishu_portal.html").read_text(encoding="utf-8")
        posts_json = (FIXTURES_DIR / "saas_feishu_posts.json").read_text(encoding="utf-8")
        company = {
            "company_id": "songmics-home",
            "name": "致欧家居科技股份有限公司",
            "industry": "跨境电商/互联网家居",
            "cities": ["郑州市", "深圳市"],
            "career_url": "https://songmicshome.jobs.feishu.cn/",
            "saas_platform": "feishu",
            "work_schedule": "周末双休",
            "benefit_claims": [
                {
                    "label": "周末双休",
                    "evidence_quote": "4.工作时间： 双休，9:00-18:00。",
                    "evidence_url": "https://songmicshome.jobs.feishu.cn/index/position/7705970818661189930/detail",
                    "verified_at": "2026-11-18",
                }
            ],
            "status": "active",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            u = str(request.url)
            if u == "https://songmicshome.jobs.feishu.cn/":
                return httpx.Response(200, text=portal_html)
            if u.endswith("/api/v1/csrf/token"):
                return httpx.Response(200, json={"code": 0, "data": {"token": "mock-csrf"}})
            if u.endswith("/api/v1/search/job/posts"):
                self.assertEqual(request.headers.get("x-csrf-token"), "mock-csrf")
                self.assertEqual(request.headers.get("Website-Path"), "index")
                return httpx.Response(200, text=posts_json)
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _init_temp_workspace(root, company)
            cfg = {
                "source_id": "ent_songmics_feishu",
                "name": "致欧家居科技股份有限公司（飞书招聘）",
                "category": "enterprise",
                "province": "河南省",
                "city": "郑州市",
                "adapter": "feishu_hire",
                "base_url": "https://songmicshome.jobs.feishu.cn",
                "list_urls": ["https://songmicshome.jobs.feishu.cn/"],
                "enabled": True,
                "url_verified": True,
            }
            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(handler)) as client:
                adapter = FeishuHireAdapter(cfg, http_client=client, state_store=store)
                s1 = adapter.run(dry_run=False, max_items=10)
                self.assertEqual(s1.errors, [])
                self.assertEqual(s1.announcements_fetched, 2)
                self.assertEqual(s1.new_count, 2)
                self.assertEqual(validate_workspace(root), [])

                # Prefer Zhengzhou job first and verify zero raw_inbox pollution
                self.assertFalse((root / "raw_inbox").exists())
                jobs_data = json.loads((root / "data" / "jobs.json").read_text(encoding="utf-8"))["items"]
                self.assertEqual(jobs_data[0]["city"], "郑州市")
                self.assertEqual(jobs_data[0]["province"], "河南省")
                self.assertEqual(jobs_data[0]["enterprise_meta"]["work_schedule"], "周末双休")
                self.assertEqual(
                    jobs_data[0]["source_url"],
                    "https://songmicshome.jobs.feishu.cn/index/position/7705970818661189930/detail",
                )

                # Re-run should deduplicate cleanly
                s2 = adapter.run(dry_run=False, max_items=10)
                self.assertEqual(s2.new_count, 0)
                self.assertEqual(s2.unchanged_count, 2)

    def test_greenhouse_adapter_filters_china_and_remote_only(self) -> None:
        gh_json = (FIXTURES_DIR / "saas_greenhouse_jobs.json").read_text(encoding="utf-8")
        company = {
            "company_id": "canonical-ubuntu",
            "name": "Canonical",
            "industry": "开源操作系统与云基础设施",
            "cities": ["远程", "北京市"],
            "career_url": "https://canonical.com/careers",
            "saas_platform": "greenhouse",
            "work_schedule": "弹性工作",
            "benefit_claims": [
                {
                    "label": "全员远程优先",
                    "evidence_quote": "Canonical has been a remote-first company since its inception in 2004.",
                    "evidence_url": "https://job-boards.greenhouse.io/canonical/jobs/7267406",
                    "verified_at": "2026-11-18",
                }
            ],
            "status": "active",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=gh_json)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _init_temp_workspace(root, company)
            cfg = {
                "source_id": "ent_canonical_greenhouse",
                "name": "Canonical（Greenhouse）",
                "category": "enterprise",
                "province": "全国/远程",
                "city": "远程",
                "adapter": "greenhouse",
                "base_url": "https://boards-api.greenhouse.io",
                "list_urls": ["https://boards-api.greenhouse.io/v1/boards/canonical/jobs?content=true"],
                "enabled": True,
                "url_verified": True,
            }
            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(handler)) as client:
                adapter = GreenhouseAdapter(cfg, http_client=client, state_store=store)
                summary = adapter.run(dry_run=False, max_items=10)
                self.assertEqual(summary.new_count, 2)
                self.assertEqual(validate_workspace(root), [])

                items = json.loads((root / "data" / "jobs.json").read_text(encoding="utf-8"))["items"]
                titles = [j["title"] for j in items]
                self.assertIn("Beijing Office Administrator", titles)
                self.assertIn("Embedded Linux Field Engineer (Mandarin speaking)", titles)
                self.assertNotIn("Channel Partner Sales Executive, Japan", titles)
                self.assertNotIn("US East Account Executive", titles)

                beijing_job = next(j for j in items if j["title"] == "Beijing Office Administrator")
                self.assertEqual(beijing_job["city"], "北京市")
                self.assertEqual(beijing_job["enterprise_meta"]["remote_policy"], "线下办公")
                remote_job = next(j for j in items if "Mandarin" in j["title"])
                self.assertEqual(remote_job["city"], "远程")
                self.assertEqual(remote_job["enterprise_meta"]["remote_policy"], "可远程")

    def test_lever_adapter_offline(self) -> None:
        lv_json = (FIXTURES_DIR / "saas_lever_postings.json").read_text(encoding="utf-8")
        company = {
            "company_id": "example-lever",
            "name": "示例数据库公司",
            "industry": "基础软件",
            "cities": ["上海市"],
            "career_url": "https://jobs.lever.co/exampleco",
            "saas_platform": "lever",
            "work_schedule": "弹性工作",
            "benefit_claims": [
                {
                    "label": "混合/远程办公",
                    "evidence_quote": "Hybrid work model/Remote work opportunities available.",
                    "evidence_url": "https://jobs.lever.co/exampleco/lever-cn-001",
                    "verified_at": "2026-11-18",
                }
            ],
            "status": "active",
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _init_temp_workspace(root, company)
            cfg = {
                "source_id": "ent_lever_test",
                "company_id": "example-lever",
                "name": "示例 Lever 源",
                "category": "enterprise",
                "province": "上海市",
                "city": "上海市",
                "adapter": "lever",
                "base_url": "https://api.lever.co",
                "list_urls": ["https://api.lever.co/v0/postings/exampleco?mode=json"],
                "enabled": True,
                "url_verified": True,
            }
            with PoliteHttpClient(
                min_interval_sec=0.0,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, text=lv_json)),
            ) as client:
                adapter = LeverAdapter(cfg, http_client=client, state_store=store)
                summary = adapter.run(dry_run=False, max_items=10)
                self.assertEqual(summary.new_count, 1)
                self.assertEqual(validate_workspace(root), [])
                items = json.loads((root / "data" / "jobs.json").read_text(encoding="utf-8"))["items"]
                self.assertEqual(items[0]["city"], "上海市")
                self.assertEqual(items[0]["enterprise_meta"]["remote_policy"], "可远程")

    def test_moka_adapter_offline(self) -> None:
        moka_html = (FIXTURES_DIR / "saas_moka_portal.html").read_text(encoding="utf-8")
        company = {
            "company_id": "moka-hr",
            "name": "北京希瑞亚斯科技有限公司",
            "industry": "企业级 HR SaaS",
            "cities": ["北京市", "广州市"],
            "career_url": "https://app.mokahr.com/social-recruitment/moka/118263",
            "saas_platform": "moka",
            "work_schedule": "弹性工作",
            "benefit_claims": [
                {
                    "label": "弹性办公",
                    "evidence_quote": "花式团建、弹性办公、无限零食下午茶与节日礼遇；一起赢，也好好生活",
                    "evidence_url": "https://app.mokahr.com/social-recruitment/moka/118263",
                    "verified_at": "2026-11-18",
                }
            ],
            "status": "active",
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _init_temp_workspace(root, company)
            cfg = {
                "source_id": "ent_moka_self",
                "name": "Moka 社招",
                "category": "enterprise",
                "province": "北京市",
                "city": "多城市",
                "adapter": "moka",
                "base_url": "https://app.mokahr.com",
                "list_urls": ["https://app.mokahr.com/social-recruitment/moka/118263"],
                "enabled": True,
                "url_verified": True,
            }
            with PoliteHttpClient(
                min_interval_sec=0.0,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, text=moka_html)),
            ) as client:
                adapter = MokaAdapter(cfg, http_client=client, state_store=store)
                summary = adapter.run(dry_run=False, max_items=10)
                self.assertEqual(summary.new_count, 2)
                self.assertEqual(validate_workspace(root), [])
                items = json.loads((root / "data" / "jobs.json").read_text(encoding="utf-8"))["items"]
                self.assertEqual(items[0]["city"], "北京市")
                self.assertEqual(items[1]["city"], "广州市")
                self.assertEqual(
                    items[0]["source_url"],
                    "https://app.mokahr.com/social-recruitment/moka/118263#/job/job-moka-1",
                )

    def test_emq_and_dify_official_json_adapters_offline(self) -> None:
        emq_json = (FIXTURES_DIR / "saas_emq_jobs.json").read_text(encoding="utf-8")
        dify_json = (FIXTURES_DIR / "saas_dify_jobs.json").read_text(encoding="utf-8")

        emq_comp = {
            "company_id": "emq-technologies",
            "name": "杭州映云科技有限公司",
            "industry": "开源物联网与基础软件",
            "cities": ["北京市", "上海市", "杭州市"],
            "career_url": "https://careers.emqx.com/zh/alljobs",
            "saas_platform": "custom",
            "work_schedule": "弹性工作",
            "benefit_claims": [
                {
                    "label": "不打卡/拒绝996",
                    "evidence_quote": "拒绝无意义的打卡和「996」，我们希望你高效工作，同时也要追求丰富的人生。",
                    "evidence_url": "https://careers.emqx.com/zh",
                    "verified_at": "2026-11-18",
                }
            ],
            "status": "active",
        }
        dify_comp = {
            "company_id": "dify-langgenius",
            "name": "苏州语灵人工智能科技有限公司",
            "industry": "开源 AI 应用开发平台",
            "cities": ["苏州市", "远程"],
            "career_url": "https://join.dify.ai/roles",
            "saas_platform": "custom",
            "work_schedule": "弹性工作",
            "benefit_claims": [
                {
                    "label": "混合与远程办公",
                    "evidence_quote": "Hybrid policy: in-office when collaboration matters, remote when it doesn't.",
                    "evidence_url": "https://join.dify.ai/",
                    "verified_at": "2026-11-18",
                }
            ],
            "status": "active",
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _init_temp_workspace(root, emq_comp)
            (root / "data" / "companies.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "updated_at": "2026-11-18T17:00:00+08:00",
                        "items": [emq_comp, dify_comp],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            emq_cfg = {
                "source_id": "ent_emq_jobs",
                "name": "EMQ 官网",
                "category": "enterprise",
                "province": "浙江省",
                "city": "多城市",
                "adapter": "emq_jobs",
                "base_url": "https://careers.emqx.com",
                "list_urls": ["https://community-sites.emqx.com/api/v1/jobs?language=zh&enable=1&limit=50"],
                "enabled": True,
                "url_verified": True,
            }
            dify_cfg = {
                "source_id": "ent_dify_jobs",
                "name": "Dify 官网",
                "category": "enterprise",
                "province": "江苏省",
                "city": "多城市",
                "adapter": "dify_jobs",
                "base_url": "https://join.dify.ai",
                "list_urls": ["https://qcnurokxgtyuimuixztr.supabase.co/functions/v1/public-jobs?lang=zh"],
                "enabled": True,
                "url_verified": True,
            }

            with PoliteHttpClient(
                min_interval_sec=0.0,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, text=emq_json)),
            ) as c1:
                s_emq = EmqJobsAdapter(emq_cfg, http_client=c1, state_store=store).run(dry_run=False, max_items=10)
                self.assertEqual(s_emq.new_count, 1)

            with PoliteHttpClient(
                min_interval_sec=0.0,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, text=dify_json)),
            ) as c2:
                s_dify = DifyJobsAdapter(dify_cfg, http_client=c2, state_store=store).run(dry_run=False, max_items=10)
                self.assertEqual(s_dify.new_count, 1)

            self.assertEqual(validate_workspace(root), [])
            items = json.loads((root / "data" / "jobs.json").read_text(encoding="utf-8"))["items"]
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["source_type"], "company_official")
            self.assertEqual(items[0]["enterprise_meta"]["salary_range"], "2.5万-4万 × 14薪")
            self.assertEqual(items[1]["city"], "苏州市")
            self.assertEqual(items[1]["enterprise_meta"]["remote_policy"], "可远程")


if __name__ == "__main__":
    unittest.main()
