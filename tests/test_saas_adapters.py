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
    BeisenAdapter,
    DifyJobsAdapter,
    EmqJobsAdapter,
    FeishuHireAdapter,
    GreenhouseAdapter,
    HanweiJobsAdapter,
    HotjobAdapter,
    LeverAdapter,
    MokaAdapter,
    WeihuaJobsAdapter,
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

    def test_beisen_adapter_parses_portal_and_jobs(self) -> None:
        portal_html = (FIXTURES_DIR / "beisen_portal.html").read_text(encoding="utf-8")
        jobs_json = (FIXTURES_DIR / "beisen_jobs.json").read_text(encoding="utf-8")
        company = {
            "company_id": "pingxingxian-edu",
            "name": "郑州一起学教育科技有限公司（平行线教育）",
            "industry": "教育科技与培优",
            "cities": ["郑州市", "洛阳市"],
            "career_url": "https://zzpxx.zhiye.com/",
            "saas_platform": "beisen",
            "work_schedule": "弹性工作",
            "benefit_claims": [
                {
                    "label": "弹性工作与商业保险",
                    "evidence_quote": "全面的福利关怀 五险一金及补充商业保险全面保障；弹性的工作时间和充足假期。",
                    "evidence_url": "https://zzpxx.zhiye.com/",
                    "verified_at": "2026-11-21",
                }
            ],
            "status": "active",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/":
                return httpx.Response(200, text=portal_html)
            if request.url.path == "/api/Jobad/GetJobAdPageList":
                body = json.loads(request.content.decode("utf-8"))
                self.assertEqual(body["PortalId"], "ee619e4d-7e76-46a5-aec6-b429b3c9e1eb")
                if int(body.get("PageIndex", 0)) == 0:
                    return httpx.Response(200, text=jobs_json)
                return httpx.Response(200, text=json.dumps({"Code": 200, "Count": 3, "Data": []}))
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _init_temp_workspace(root, company)
            src_cfg = {
                "source_id": "ent_zzpxx_beisen",
                "name": "平行线教育（北森）",
                "category": "enterprise",
                "province": "河南省",
                "city": "郑州市",
                "adapter": "beisen",
                "base_url": "https://zzpxx.zhiye.com",
                "list_urls": ["https://zzpxx.zhiye.com/"],
                "enabled": True,
                "url_verified": True,
            }
            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(handler)) as client:
                summary = BeisenAdapter(src_cfg, http_client=client, state_store=store).run(dry_run=False, max_items=10)
            self.assertEqual(summary.new_count, 2)
            self.assertEqual(validate_workspace(root), [])
            items = json.loads((root / "data" / "jobs.json").read_text(encoding="utf-8"))["items"]
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["city"], "郑州市")
            self.assertEqual(items[0]["published_at"], "2026-10-13")
            self.assertEqual(items[0]["enterprise_meta"]["work_schedule"], "弹性工作")
            self.assertEqual(items[0]["source_url"], "https://zzpxx.zhiye.com/SocialDetail?id=230947423")
            self.assertEqual(items[1]["city"], "洛阳市")

    def test_hotjob_hanwei_and_weihua_adapters(self) -> None:
        pos_json = (FIXTURES_DIR / "hotjob_positions.json").read_text(encoding="utf-8")
        det_json = (FIXTURES_DIR / "hotjob_detail.json").read_text(encoding="utf-8")
        wh_json = (FIXTURES_DIR / "weihua_filter.json").read_text(encoding="utf-8")
        hw_html = (FIXTURES_DIR / "hanwei_jobs.html").read_text(encoding="utf-8")

        autobio = {
            "company_id": "autobio-zhengzhou",
            "name": "郑州安图生物工程股份有限公司",
            "industry": "体外诊断",
            "cities": ["郑州市"],
            "career_url": "https://wecruit.hotjob.cn/SU62302d11bef57c4972f79a6b/pb/index.html",
            "saas_platform": "custom",
            "work_schedule": "其他",
            "benefit_claims": [
                {
                    "label": "五天八小时与五险一金",
                    "evidence_quote": "1.工作时间：五天八小时工作制；4.缴纳五险一金。",
                    "evidence_url": "https://www.autobio-bj.com.cn/Human/index/fid/69/cid/42/lid/71/id/262.html",
                    "verified_at": "2026-11-21",
                }
            ],
            "status": "active",
        }
        hanwei = {
            "company_id": "hanwei-tech",
            "name": "汉威科技集团股份有限公司",
            "industry": "物联网与传感器",
            "cities": ["郑州市"],
            "career_url": "https://hanwei.cn/join_us.html",
            "saas_platform": "custom",
            "work_schedule": "周末双休",
            "benefit_claims": [
                {
                    "label": "周末双休",
                    "evidence_quote": "双休；五险一金；带薪年假；正常节假日及福利；",
                    "evidence_url": "https://hanwei.cn/join_us.html",
                    "verified_at": "2026-11-21",
                }
            ],
            "status": "active",
        }
        weihua = {
            "company_id": "weihua-group",
            "name": "河南卫华重型机械股份有限公司（卫华集团）",
            "industry": "智能起重装备",
            "cities": ["新乡市"],
            "career_url": "https://job.craneweihua.com/recruit?type=1&unit_id=4",
            "saas_platform": "custom",
            "work_schedule": "其他",
            "benefit_claims": [
                {
                    "label": "免费餐厅公寓",
                    "evidence_quote": "营养餐厅 免费就餐 一日四餐 智慧公寓 免费住宿",
                    "evidence_url": "https://www.cranewh.com/talent_development",
                    "verified_at": "2026-11-21",
                }
            ],
            "status": "active",
        }

        for expected in ("beisen", "hotjob", "hanwei_jobs", "gltech_jobs", "suntront_jobs", "xyyl_jobs", "weihua_jobs", "lomon_jobs"):
            self.assertIn(expected, ADAPTER_REGISTRY)

        def hotjob_handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode("utf-8")
            if "listPosition/" in request.url.path:
                if "recruitType=2" in body:
                    return httpx.Response(200, text=pos_json)
                return httpx.Response(200, text=json.dumps({"data": {"pageForm": {"pageData": []}}}))
            if "listPositionDetail/" in request.url.path:
                return httpx.Response(200, text=det_json)
            return httpx.Response(404)

        def weihua_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/recruit":
                return httpx.Response(200, text="<html></html>")
            if request.url.path == "/filter":
                body = request.content.decode("utf-8")
                if "unit_id=4" in body and "type=1" in body and "page=1" in body:
                    return httpx.Response(200, text=wh_json)
                return httpx.Response(200, text=json.dumps({"success": True, "count": 0, "data": []}))
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _init_temp_workspace(root, autobio)
            (root / "data" / "companies.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "updated_at": "2026-11-21T13:00:00+08:00",
                        "items": [autobio, hanwei, weihua],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            hj_cfg = {
                "source_id": "ent_autobio_hotjob",
                "name": "安图生物（大易Hotjob）",
                "category": "enterprise",
                "province": "河南省",
                "city": "郑州市",
                "adapter": "hotjob",
                "base_url": "https://wecruit.hotjob.cn/SU62302d11bef57c4972f79a6b/pb/index.html",
                "list_urls": ["https://wecruit.hotjob.cn/SU62302d11bef57c4972f79a6b/pb/index.html"],
                "enabled": True,
                "url_verified": True,
            }
            hw_cfg = {
                "source_id": "ent_hanwei_jobs",
                "name": "汉威科技",
                "category": "enterprise",
                "province": "河南省",
                "city": "郑州市",
                "adapter": "hanwei_jobs",
                "base_url": "https://hanwei.cn",
                "list_urls": ["https://hanwei.cn/join_us.html"],
                "enabled": True,
                "url_verified": True,
            }
            wh_cfg = {
                "source_id": "ent_weihua_jobs",
                "name": "卫华集团",
                "category": "enterprise",
                "province": "河南省",
                "city": "新乡市",
                "adapter": "weihua_jobs",
                "base_url": "https://job.craneweihua.com",
                "list_urls": ["https://job.craneweihua.com/recruit?type=1&unit_id=4"],
                "enabled": True,
                "url_verified": True,
            }

            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(hotjob_handler)) as c1:
                self.assertEqual(HotjobAdapter(hj_cfg, http_client=c1, state_store=store).run(dry_run=False, max_items=10).new_count, 1)
            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text=hw_html))) as c2:
                self.assertEqual(HanweiJobsAdapter(hw_cfg, http_client=c2, state_store=store).run(dry_run=False, max_items=10).new_count, 1)
            with PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(weihua_handler)) as c3:
                self.assertEqual(WeihuaJobsAdapter(wh_cfg, http_client=c3, state_store=store).run(dry_run=False, max_items=10).new_count, 1)

            self.assertEqual(validate_workspace(root), [])
            items = json.loads((root / "data" / "jobs.json").read_text(encoding="utf-8"))["items"]
            self.assertEqual(len(items), 3)
            self.assertIn("/mc/detail?postId=6afecf8ce71196a618f0e0c3&recruitType=2", items[0]["source_url"])
            self.assertEqual(items[1]["published_at"], "2026-07-17")
            self.assertEqual(items[1]["enterprise_meta"]["work_schedule"], "周末双休")
            self.assertEqual(items[2]["city"], "新乡市")

    def test_salary_extraction_positive_negative_and_native_fields(self) -> None:
        from scrapers.adapters.saas import (
            BeisenAdapter,
            FeishuHireAdapter,
            GltechJobsAdapter,
            JuicefsJobsAdapter,
            LomonJobsAdapter,
            MokaAdapter,
            SuntrontJobsAdapter,
            WeihuaJobsAdapter,
            XyylJobsAdapter,
            extract_salary_range,
        )

        # Positive text cases
        self.assertEqual(extract_salary_range("薪资范围：2.5万-4万 × 14薪"), "2.5万-4万 × 14薪")
        self.assertEqual(
            extract_salary_range("薪资范围：1.2万-2万 × 12-16薪（具体业绩及公司政策调整）"),
            "1.2万-2万 × 12-16薪",
        )
        self.assertEqual(
            extract_salary_range("【薪资福利】\n3.薪资体系：基本工资+课时费+带生奖+奖金激励，年薪保底40W到100W+。"),
            "年薪：保底40W到100W+",
        )
        self.assertEqual(
            extract_salary_range("1. 月薪：15~27K，参考年薪：20W~33W详情面谈（不定时工作制）"),
            "月薪：15~27K，参考年薪：20W~33W",
        )
        self.assertEqual(extract_salary_range("（税前5k-7k左右，需根据不同城市调整）"), "税前5k-7k")
        self.assertEqual(extract_salary_range("实习薪资：140元/天，月度发放"), "140元/天，月度发放")
        self.assertEqual(extract_salary_range("时薪25元一小时~"), "时薪：25元一小时")
        self.assertEqual(extract_salary_range("年薪：6-10万"), "年薪：6-10万")
        self.assertEqual(extract_salary_range("目前大部分销售员的年收入在6-15万元之间。"), "年收入：6-15万元")
        self.assertEqual(extract_salary_range("本科15万·硕士20万"), "本科15万·硕士20万")
        self.assertEqual(extract_salary_range("安家费20万元，享受周末双休"), "安家费：20万元")

        # Negative / false-positive blocking cases
        self.assertIsNone(extract_salary_range("具市场竞争力的薪资架构 · 早期期权\n¥500/季健身补贴\n¥2000/月语言学习补贴\n$1000/月 AI 体验补贴"))
        self.assertIsNone(extract_salary_range("【薪资福利】\n五险一金、年度体检"))
        self.assertIsNone(extract_salary_range("1.高于行业平均水平，竞争力底薪+全勤奖+专项奖金+年终奖+其它"))
        self.assertIsNone(extract_salary_range("年薪：面议\n任职要求：5年以上开发经验"))
        self.assertIsNone(extract_salary_range("岗位要求：熟悉72K×72K地图分辨率，500kV变电站设计，完成年度签约额2000万元目标"))

        # Native Feishu min_salary/max_salary + portal degree policy vs negotiable PhD / intern exclusion
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rigol_company = {
                "company_id": "rigol-inc",
                "name": "普源精电科技股份有限公司",
                "aliases": ["普源精电", "RIGOL"],
                "industry": "电子测量仪器",
                "cities": ["苏州市", "多城市"],
                "career_url": "https://rigolportal.jobs.feishu.cn/campus/",
                "saas_platform": "feishu",
                "work_schedule": "弹性工作",
                "benefit_claims": [],
                "status": "active",
            }
            sosen_company = {
                "company_id": "sosen-inc",
                "name": "深圳市崧盛电子股份有限公司",
                "aliases": ["崧盛电子"],
                "industry": "LED驱动电源",
                "cities": ["深圳市", "中山市"],
                "career_url": "https://sosen.zhiye.com/",
                "saas_platform": "beisen",
                "work_schedule": "周末双休",
                "benefit_claims": [],
                "status": "active",
            }
            juicedata_company = {
                "company_id": "juicedata-inc",
                "name": "杭州果汁数据科技有限公司",
                "aliases": ["JuiceFS"],
                "industry": "分布式存储",
                "cities": ["杭州市", "多城市"],
                "career_url": "https://juicefs.com/zh-cn/careers",
                "saas_platform": "custom",
                "work_schedule": "其他",
                "benefit_claims": [],
                "status": "active",
            }
            xyyl_company = {
                "company_id": "xiangyu-medical",
                "name": "河南翔宇医疗设备股份有限公司",
                "aliases": ["翔宇医疗"],
                "industry": "康复医疗器械",
                "cities": ["安阳市"],
                "career_url": "https://www.xyyl.com/jrwm.html",
                "saas_platform": "custom",
                "work_schedule": "周末双休",
                "benefit_claims": [],
                "status": "active",
            }
            moka_company = {
                "company_id": "moka-hr",
                "name": "北京希瑞亚斯科技有限公司",
                "aliases": ["Moka"],
                "industry": "HR SaaS",
                "cities": ["北京市"],
                "career_url": "https://app.mokahr.com/social-recruitment/moka/118263",
                "saas_platform": "moka",
                "work_schedule": "周末双休",
                "benefit_claims": [],
                "status": "active",
            }
            store = _init_temp_workspace(root, rigol_company)
            (root / "data" / "companies.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "updated_at": "2026-11-21T13:00:00+08:00",
                        "items": [rigol_company, sosen_company, juicedata_company, xyyl_company, moka_company],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            portal_html = (
                '<script id="js-websiteInfo">{"website_info":{"path":"campus"}}</script>'
                '"⭐研发岗位：本科20-30万，硕士30-50万，博士及优秀者面议"'
                '"⭐数据科学家：要求博士，薪资面议"'
            )
            policies, neg_roles = FeishuHireAdapter._extract_portal_salary_policies(portal_html)
            fh_adapter = FeishuHireAdapter(
                {
                    "source_id": "ent_rigol_feishu",
                    "name": "普源精电",
                    "category": "enterprise",
                    "province": "江苏省",
                    "city": "多城市",
                    "adapter": "feishu_hire",
                    "base_url": "https://rigolportal.jobs.feishu.cn",
                    "list_urls": ["https://rigolportal.jobs.feishu.cn/campus/"],
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text="{}"))),
                state_store=store,
            )
            fh_jobs = fh_adapter.parse_posts_payload(
                [
                    {
                        "id": "7686",
                        "title": "产品制造工程师-2027届",
                        "description": "负责NPI开发",
                        "requirement": "本科及以上",
                        "city_list": [{"name": "苏州"}],
                        "job_post_info": {"min_salary": 15, "max_salary": 25, "currency": None},
                    },
                    {
                        "id": "7688",
                        "title": "热设计工程师",
                        "description": "负责散热仿真",
                        "requirement": "硕士优先",
                        "city_list": [{"name": "苏州"}],
                        "job_post_info": {"min_salary": None, "max_salary": None, "currency": None},
                    },
                    {
                        "id": "7690",
                        "title": "数据科学家-2027届",
                        "description": "算法研究",
                        "requirement": "博士",
                        "city_list": [{"name": "苏州"}],
                        "job_post_info": {"min_salary": None, "max_salary": None, "currency": None},
                    },
                    {
                        "id": "7691",
                        "title": "实习生",
                        "description": "协助研发测试",
                        "requirement": "在校生",
                        "city_list": [{"name": "苏州"}],
                        "job_post_info": {"min_salary": None, "max_salary": None, "currency": None},
                    },
                ],
                portal_origin="https://rigolportal.jobs.feishu.cn",
                website_path="campus",
                portal_policies=policies,
                negotiable_roles=neg_roles,
            )
            self.assertEqual(fh_jobs[0]["enterprise_meta"]["salary_range"], "15K-25K/月")
            self.assertEqual(fh_jobs[1]["enterprise_meta"]["salary_range"], "官网校招政策：研发岗本科20-30万，硕士30-50万")
            self.assertIsNone(fh_jobs[2]["enterprise_meta"]["salary_range"])
            self.assertIsNone(fh_jobs[3]["enterprise_meta"]["salary_range"])

            # Beisen: Sosen campus policy + intern daily allowance vs Keyence irregular hours
            bs_adapter = BeisenAdapter(
                {
                    "source_id": "ent_sosen_beisen",
                    "name": "崧盛电子",
                    "category": "enterprise",
                    "province": "广东省",
                    "city": "深圳市",
                    "adapter": "beisen",
                    "base_url": "https://sosen.zhiye.com",
                    "list_urls": ["https://sosen.zhiye.com/"],
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text="{}"))),
                state_store=store,
            )
            bs_jobs = bs_adapter.parse_jobad_list(
                [
                    {
                        "JobAdId": "10229",
                        "JobAdName": "电源工程师（深圳+中山）(J10229)",
                        "CategoryId": 2,
                        "Duty": "负责驱动电源研发",
                        "Require": "2027届本科",
                        "Salary": "面议",
                        "Status": 1,
                    },
                    {
                        "JobAdId": "10223",
                        "JobAdName": "软件测试实习生（中山，双休）(J10223)",
                        "CategoryId": 3,
                        "Duty": "负责嵌入式测试",
                        "Require": "3、160元/天 实习津贴 + 实习证明",
                        "Status": 1,
                    },
                ],
                portal_origin="https://sosen.zhiye.com",
            )
            self.assertEqual(bs_jobs[0]["enterprise_meta"]["salary_range"], "官网校招政策：综合年薪8-15万元/年（深圳房补1200元/月）")
            self.assertEqual(bs_jobs[1]["enterprise_meta"]["salary_range"], "160元/天 实习津贴")
            self.assertEqual(bs_jobs[1]["city"], "中山市")

            # Moka structured minSalary/maxSalary
            mk_adapter = MokaAdapter(
                {
                    "source_id": "ent_moka_self",
                    "name": "Moka",
                    "category": "enterprise",
                    "province": "北京市",
                    "city": "北京市",
                    "adapter": "moka",
                    "base_url": "https://app.mokahr.com",
                    "list_urls": ["https://app.mokahr.com/social-recruitment/moka/118263"],
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text="{}"))),
                state_store=store,
            )
            moka_html = '<input type="hidden" id="init-data" value="{&quot;jobs&quot;:[{&quot;id&quot;:&quot;j1&quot;,&quot;status&quot;:&quot;open&quot;,&quot;title&quot;:&quot;后端工程师&quot;,&quot;minSalary&quot;:20,&quot;maxSalary&quot;:35,&quot;location&quot;:{&quot;country&quot;:&quot;中国&quot;,&quot;cityId&quot;:110100,&quot;address&quot;:&quot;朝阳区&quot;}}]}">'
            mk_jobs = mk_adapter.parse_portal_html(moka_html, "https://app.mokahr.com/social-recruitment/moka/118263")
            self.assertEqual(mk_jobs[0]["enterprise_meta"]["salary_range"], "20K-35K/月")

            # JuicefsJobsAdapter
            jf_html = """
            <html><body>
              <h2>工作机会</h2>
              <p>核心系统开发工程师</p>
              <h2>目标与职责</h2><p>核心系统研发</p>
              <h2>任职要求</h2><p>熟悉分布式存储</p>
              <h2>工作地点与薪资</h2>
              <p>工作地点在杭州，经验丰富的候选人可以申请远程</p>
              <p>薪酬：30K-60K/月，以及早期员工丰厚期权</p>
              <p>解决方案架构师</p>
              <h2>目标与职责</h2><p>客户售前支持</p>
              <h2>任职要求</h2><p>3年以上架构经验</p>
              <h2>工作地点与薪资</h2>
              <p>工作地点：北京、上海、深圳</p>
              <p>30K~40K/月，以及早期员工丰厚期权</p>
            </body></html>
            """
            jf_adapter = JuicefsJobsAdapter(
                {
                    "source_id": "ent_juicedata_jobs",
                    "name": "JuiceFS",
                    "category": "enterprise",
                    "province": "浙江省",
                    "city": "多城市",
                    "adapter": "juicefs_jobs",
                    "base_url": "https://juicefs.com",
                    "list_urls": ["https://juicefs.com/zh-cn/careers"],
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text=jf_html))),
                state_store=store,
            )
            jf_jobs = jf_adapter.fetch_jobs(max_items=10)
            self.assertEqual(len(jf_jobs), 2)
            self.assertEqual(jf_jobs[0]["enterprise_meta"]["salary_range"], "30K-60K/月")
            self.assertEqual(jf_jobs[0]["enterprise_meta"]["remote_policy"], "可远程")
            self.assertEqual(jf_jobs[1]["enterprise_meta"]["salary_range"], "30K~40K/月")

            # XyylJobsAdapter /rcwh.html policy attribution when detail says 面议
            def xyyl_handler(req: httpx.Request) -> httpx.Response:
                u = str(req.url)
                if "rcwh.html" in u:
                    return httpx.Response(200, text="<p>薪资待遇：4000-20000 元/月。一次性购房补贴3-12万元。</p>")
                if "xyzp.html" in u or "jrwm.html" in u:
                    return httpx.Response(200, text='<a href="/xyzp_detail/101.html">硬件工程师</a>')
                return httpx.Response(200, text="<html><body><main>硬件工程师\n面议\n最低学历：本科\n工作地区：安阳</main></body></html>")

            xy_adapter = XyylJobsAdapter(
                {
                    "source_id": "ent_xyyl_jobs",
                    "name": "翔宇医疗",
                    "category": "enterprise",
                    "province": "河南省",
                    "city": "安阳市",
                    "adapter": "xyyl_jobs",
                    "base_url": "https://www.xyyl.com",
                    "list_urls": ["https://www.xyyl.com/xyzp.html"],
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(xyyl_handler)),
                state_store=store,
            )
            xy_jobs = xy_adapter.fetch_jobs(max_items=5)
            self.assertEqual(xy_jobs[0]["enterprise_meta"]["salary_range"], "官网人才政策：月薪4000-20000元/月（购房补贴3-12万元）")

            # Gltech / Lomon / Suntront / Weihua salary & body extraction checks
            gl_adapter = GltechJobsAdapter(
                {
                    "source_id": "ent_gltech_jobs",
                    "name": "光力科技",
                    "category": "enterprise",
                    "province": "河南省",
                    "city": "郑州市",
                    "adapter": "gltech_jobs",
                    "base_url": "https://www.gltech.cn",
                    "list_urls": ["https://www.gltech.cn/recruitment/"],
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text="{}"))),
                state_store=store,
            )
            self.assertEqual(GltechJobsAdapter.__name__, "GltechJobsAdapter")
            gl_jobs = gl_adapter.parse_portal_html(
                '<li class="xz-a019-item"><div>嵌入式工程师</div><div>8-15k</div><div>工作地点：郑州</div></li>'
                '<li class="xz-a019-item"><div>高级顾问</div><div>面议</div><div>工作地点：郑州</div></li>',
                "https://www.gltech.cn/recruitment/",
            )
            self.assertEqual(gl_jobs[0]["enterprise_meta"]["salary_range"], "8-15k")
            self.assertIsNone(gl_jobs[1]["enterprise_meta"]["salary_range"])

            # Dify false-positive prevention in DifyJobsAdapter
            from scrapers.adapters.saas import DifyJobsAdapter
            dify_company = {
                "company_id": "dify-langgenius",
                "name": "Dify",
                "aliases": ["Dify"],
                "industry": "AI",
                "cities": ["远程"],
                "career_url": "https://join.dify.ai/",
                "saas_platform": "custom",
                "work_schedule": "周末双休",
                "benefit_claims": [],
                "status": "active",
            }
            (root / "data" / "companies.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "updated_at": "2026-11-21T13:00:00+08:00",
                        "items": [dify_company],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            dify_ad = DifyJobsAdapter(
                {
                    "source_id": "ent_dify_jobs",
                    "name": "Dify",
                    "category": "enterprise",
                    "province": "全国/远程",
                    "city": "远程",
                    "adapter": "dify_jobs",
                    "base_url": "https://join.dify.ai",
                    "list_urls": ["https://join.dify.ai/api/jobs"],
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text="{}"))),
                state_store=store,
            )
            dify_jobs = dify_ad.parse_api_payload(
                {
                    "jobs": [
                        {
                            "id": "d1",
                            "title": "解决方案工程师",
                            "location": "Remote / China",
                            "benefits": "<p>具市场竞争力的薪资架构 · 早期期权</p><p>¥500/季健身补贴</p><p>¥2000/月语言学习补贴</p>",
                            "content": "<p>负责客户技术方案</p>",
                        }
                    ]
                }
            )
            self.assertIsNone(dify_jobs[0]["enterprise_meta"]["salary_range"])

            # Lomon, Suntront & Weihua checks
            lm_ad = LomonJobsAdapter(
                {
                    "source_id": "ent_lomon_jobs",
                    "name": "龙佰集团",
                    "category": "enterprise",
                    "province": "河南省",
                    "city": "焦作市",
                    "adapter": "lomon_jobs",
                    "base_url": "https://www.lomonbillions.com",
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text="{}"))),
                state_store=store,
            )
            lm_ad._cached_company_profile = {**dify_company, "company_id": "lomonbillions", "name": "龙佰集团", "cities": ["焦作市"]}
            st_ad = SuntrontJobsAdapter(
                {
                    "source_id": "ent_suntront_jobs",
                    "name": "新天科技",
                    "category": "enterprise",
                    "province": "河南省",
                    "city": "郑州市",
                    "adapter": "suntront_jobs",
                    "base_url": "https://www.suntront.com",
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text="{}"))),
                state_store=store,
            )
            st_ad._cached_company_profile = {**dify_company, "company_id": "suntront-tech", "name": "新天科技", "cities": ["郑州市"]}
            st_jobs = st_ad.parse_portal_html(
                '<div class="station-item"><h3>销售经理</h3><p>目前大部分销售经理的年收入在10-35万元之间。</p></div>',
                "https://www.suntront.com/recruitment/recruitment-1.html",
            )
            self.assertEqual(st_jobs[0]["enterprise_meta"]["salary_range"], "年收入：10-35万元")
            wh_ad = WeihuaJobsAdapter(
                {
                    "source_id": "ent_weihua_jobs",
                    "name": "卫华集团",
                    "category": "enterprise",
                    "province": "河南省",
                    "city": "新乡市",
                    "adapter": "weihua_jobs",
                    "base_url": "https://job.craneweihua.com",
                },
                http_client=PoliteHttpClient(min_interval_sec=0.0, transport=httpx.MockTransport(lambda req: httpx.Response(200, text="{}"))),
                state_store=store,
            )
            wh_ad._cached_company_profile = {**dify_company, "company_id": "weihua-group", "name": "卫华集团", "cities": ["新乡市"]}
            wh_jobs = wh_ad.parse_unit_payload(
                [{"id": "w1", "name": "机械工程师", "content": "<p>本科以上</p>", "content2": "<p>负责起重机设计</p>", "address": "河南省郑州市", "depart": "研发中心"}],
                unit_name="郑州卫莱",
                default_unit_city="郑州市",
                portal_origin="https://job.craneweihua.com",
            )
            self.assertIn("负责起重机设计", wh_jobs[0]["summary"])
            self.assertEqual(wh_jobs[0]["city"], "郑州市")
            _ = lm_ad


if __name__ == "__main__":
    unittest.main()
