"""Unit tests for scripts/validate_data.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_data import ROOT_DIR, validate_workspace


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ValidateDataTests(unittest.TestCase):
    def test_real_workspace_is_valid(self) -> None:
        self.assertEqual(validate_workspace(ROOT_DIR), [])

    def test_valid_public_and_enterprise_records_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "config" / "sources.json",
                {
                    "schema_version": "1.0",
                    "sources": [
                        {
                            "source_id": "henan_test",
                            "name": "河南测试源",
                            "category": "public_institution",
                            "province": "河南省",
                            "adapter": "henan_hrss",
                            "base_url": "https://hrss.henan.gov.cn",
                            "enabled": True,
                            "url_verified": True,
                        }
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
                            "company_id": "acme-tech",
                            "name": "测试科技",
                            "industry": "软件服务",
                            "cities": ["郑州市"],
                            "career_url": "https://example.com/careers",
                            "saas_platform": "moka",
                            "work_schedule": "周末双休",
                            "benefit_claims": [
                                {
                                    "label": "周末双休",
                                    "evidence_quote": "官网招聘页原文：实行周末双休及弹性打卡制度",
                                    "evidence_url": "https://example.com/careers",
                                    "verified_at": "2026-11-18",
                                }
                            ],
                            "status": "active",
                        }
                    ],
                },
            )
            _write_json(
                root / "data" / "jobs.json",
                {
                    "schema_version": "1.0",
                    "updated_at": "2026-11-18T12:00:00+08:00",
                    "items": [
                        {
                            "id": "pub-henan-0001",
                            "category": "public_institution",
                            "title": "信息中心网络管理员",
                            "org_name": "河南省某直属事业单位",
                            "province": "河南省",
                            "city": "郑州市",
                            "source_url": "https://hrss.henan.gov.cn/example.html",
                            "source_name": "河南省人社厅",
                            "source_type": "gov_official",
                            "published_at": "2026-11-18",
                            "deadline_at": "2026-11-30",
                            "status": "active",
                            "scraped_at": "2026-11-18T12:00:00+08:00",
                            "last_seen_at": "2026-11-18T12:00:00+08:00",
                            "content_hash": "abcd1234ef56",
                            "summary": "全额拨款事业编，本科及以上，计算机类专业。",
                            "tags": ["全额事业编", "郑州", "计算机"],
                            "public_meta": {
                                "post_code": "10101",
                                "bianzhi_type": "全额拨款事业编",
                                "headcount": 2,
                                "education_min": "本科",
                                "majors": ["计算机科学与技术", "软件工程"],
                                "attachments": [],
                            },
                        },
                        {
                            "id": "ent-acme-0001",
                            "category": "enterprise",
                            "title": "后端开发工程师",
                            "org_name": "测试科技",
                            "province": "河南省",
                            "city": "郑州市",
                            "source_url": "https://example.com/careers/job-1",
                            "source_name": "测试科技官网(Moka)",
                            "source_type": "verified_saas",
                            "status": "active",
                            "scraped_at": "2026-11-18T12:00:00+08:00",
                            "last_seen_at": "2026-11-18T12:00:00+08:00",
                            "content_hash": "99887766aabb",
                            "summary": "官方JD写明周末双休，六险一金。",
                            "tags": ["周末双休", "六险一金"],
                            "enterprise_meta": {
                                "company_id": "acme-tech",
                                "work_schedule": "周末双休",
                                "remote_policy": "混合办公",
                                "benefits_verified": [
                                    {"tag": "周末双休", "evidence_quote": "JD原文：周末双休，不鼓励无效加班"}
                                ],
                            },
                        },
                    ],
                },
            )
            self.assertEqual(validate_workspace(root), [])

    def test_rejects_missing_company_fk_and_mixed_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "config" / "sources.json", {"schema_version": "1.0", "sources": []})
            _write_json(
                root / "data" / "companies.json",
                {"schema_version": "1.0", "updated_at": "2026-11-18T12:00:00+08:00", "items": []},
            )
            _write_json(
                root / "data" / "jobs.json",
                {
                    "schema_version": "1.0",
                    "updated_at": "2026-11-18T12:00:00+08:00",
                    "items": [
                        {
                            "id": "ent-bad-0001",
                            "category": "enterprise",
                            "title": "测试岗",
                            "org_name": "未知公司",
                            "province": "河南省",
                            "city": "郑州市",
                            "source_url": "https://example.com/job",
                            "source_name": "官网",
                            "source_type": "company_official",
                            "status": "active",
                            "scraped_at": "2026-11-18T12:00:00+08:00",
                            "last_seen_at": "2026-11-18T12:00:00+08:00",
                            "content_hash": "12345678",
                            "summary": "摘要",
                            "tags": [],
                            "public_meta": {
                                "bianzhi_type": "未知",
                                "education_min": "本科",
                                "majors": [],
                                "attachments": [],
                            },
                            "enterprise_meta": {
                                "company_id": "missing-company",
                                "work_schedule": "周末双休",
                                "remote_policy": "线下办公",
                                "benefits_verified": [{"tag": "双休", "evidence_quote": "原文双休"}],
                            },
                        }
                    ],
                },
            )
            errs = validate_workspace(root)
            self.assertTrue(any("public_meta must be null/omitted" in e for e in errs))
            self.assertTrue(any("does not exist in companies.json" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
