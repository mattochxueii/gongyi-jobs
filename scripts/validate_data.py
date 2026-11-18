"""Validate data/*.json and config/sources.json against project data contracts."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,78}[a-z0-9]$")
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,78}[a-z0-9]$")

JOB_CATEGORIES = {"public_institution", "enterprise"}
SOURCE_TYPES = {"gov_official", "institution_official", "company_official", "verified_saas"}
JOB_STATUSES = {"active", "expired", "closed"}
BIANZHI_TYPES = {
    "全额拨款事业编",
    "差额补贴事业编",
    "自收自支事业编",
    "控制数/备案制",
    "公开招聘(具体见公告)",
    "未知",
}
EDU_LEVELS = {"不限", "中专/高中", "大专", "本科", "硕士", "博士", "见公告"}
JOB_SCHEDULES = {"周末双休", "弹性工作", "做四休三", "大小周", "单休", "未明确"}
COMPANY_SCHEDULES = {"周末双休", "弹性工作", "做四休三", "其他"}
REMOTE_POLICIES = {"线下办公", "混合办公", "可远程", "未明确"}
SAAS_PLATFORMS = {"moka", "beisen", "feishu", "greenhouse", "lever", "workday", "custom", "unknown"}
COMPANY_STATUSES = {"active", "paused"}


def _is_valid_uri(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_valid_date(value: Any) -> bool:
    if not isinstance(value, str) or not DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _is_valid_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _check_non_empty_str(obj: dict[str, Any], key: str, prefix: str, errors: list[str], max_len: int = 500) -> None:
    val = obj.get(key)
    if not isinstance(val, str) or not val.strip():
        errors.append(f"{prefix}.{key} must be a non-empty string")
    elif len(val) > max_len:
        errors.append(f"{prefix}.{key} exceeds max length {max_len}")


def _check_optional_str(obj: dict[str, Any], key: str, prefix: str, errors: list[str], max_len: int = 500) -> None:
    val = obj.get(key)
    if val is None:
        return
    if not isinstance(val, str) or not val.strip():
        errors.append(f"{prefix}.{key} must be a non-empty string or null")
    elif len(val) > max_len:
        errors.append(f"{prefix}.{key} exceeds max length {max_len}")


def _check_enum(obj: dict[str, Any], key: str, allowed: set[str], prefix: str, errors: list[str]) -> None:
    val = obj.get(key)
    if val not in allowed:
        errors.append(f"{prefix}.{key} ({val!r}) must be one of {sorted(allowed)}")


def _check_allowed_keys(obj: dict[str, Any], required: set[str], optional: set[str], prefix: str, errors: list[str]) -> None:
    missing = required - obj.keys()
    extra = obj.keys() - (required | optional)
    if missing:
        errors.append(f"{prefix} missing required keys: {sorted(missing)}")
    if extra:
        errors.append(f"{prefix} has unexpected keys: {sorted(extra)}")


def _validate_company(item: Any, idx: int, seen_ids: set[str], errors: list[str]) -> None:
    prefix = f"companies.items[{idx}]"
    if not isinstance(item, dict):
        errors.append(f"{prefix} must be an object")
        return

    required = {
        "company_id",
        "name",
        "industry",
        "cities",
        "career_url",
        "saas_platform",
        "work_schedule",
        "benefit_claims",
        "status",
    }
    optional = {"aliases", "notes"}
    _check_allowed_keys(item, required, optional, prefix, errors)

    cid = item.get("company_id")
    if not isinstance(cid, str) or not SLUG_RE.match(cid):
        errors.append(f"{prefix}.company_id ({cid!r}) must match {SLUG_RE.pattern}")
    elif cid in seen_ids:
        errors.append(f"{prefix}.company_id ({cid!r}) is duplicated")
    else:
        seen_ids.add(cid)

    for key, max_len in (("name", 120), ("industry", 60)):
        _check_non_empty_str(item, key, prefix, errors, max_len)

    if not _is_valid_uri(item.get("career_url")):
        errors.append(f"{prefix}.career_url must be a valid http/https URI")

    _check_enum(item, "saas_platform", SAAS_PLATFORMS, prefix, errors)
    _check_enum(item, "work_schedule", COMPANY_SCHEDULES, prefix, errors)
    _check_enum(item, "status", COMPANY_STATUSES, prefix, errors)

    cities = item.get("cities")
    if not isinstance(cities, list) or not cities or not all(isinstance(c, str) and c.strip() for c in cities):
        errors.append(f"{prefix}.cities must be a non-empty list of strings")

    claims = item.get("benefit_claims")
    if not isinstance(claims, list) or not claims:
        errors.append(f"{prefix}.benefit_claims must be a non-empty list")
        return

    for c_idx, claim in enumerate(claims):
        c_prefix = f"{prefix}.benefit_claims[{c_idx}]"
        if not isinstance(claim, dict):
            errors.append(f"{c_prefix} must be an object")
            continue
        _check_allowed_keys(claim, {"label", "evidence_quote", "evidence_url", "verified_at"}, set(), c_prefix, errors)
        _check_non_empty_str(claim, "label", c_prefix, errors, 50)
        _check_non_empty_str(claim, "evidence_quote", c_prefix, errors, 300)
        if not _is_valid_uri(claim.get("evidence_url")):
            errors.append(f"{c_prefix}.evidence_url must be a valid http/https URI")
        if not _is_valid_date(claim.get("verified_at")):
            errors.append(f"{c_prefix}.verified_at must be YYYY-MM-DD")


def _validate_public_meta(meta: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(meta, dict):
        errors.append(f"{prefix}.public_meta must be an object")
        return

    p_prefix = f"{prefix}.public_meta"
    required = {"bianzhi_type", "education_min", "majors", "attachments"}
    optional = {
        "post_code",
        "department",
        "post_level",
        "headcount",
        "degree_min",
        "fresh_grad_only",
        "age_limit",
        "political_status",
        "exam_type",
        "apply_method",
        "apply_start_at",
        "written_exam_time",
        "interview_time",
        "other_requirements",
        "announcement_title",
    }
    _check_allowed_keys(meta, required, optional, p_prefix, errors)
    _check_enum(meta, "bianzhi_type", BIANZHI_TYPES, p_prefix, errors)
    _check_enum(meta, "education_min", EDU_LEVELS, p_prefix, errors)

    for opt_key, max_len in (
        ("post_code", 60),
        ("department", 120),
        ("post_level", 80),
        ("degree_min", 60),
        ("age_limit", 80),
        ("political_status", 60),
        ("exam_type", 120),
        ("apply_method", 160),
        ("written_exam_time", 120),
        ("interview_time", 120),
        ("other_requirements", 300),
        ("announcement_title", 240),
    ):
        _check_optional_str(meta, opt_key, p_prefix, errors, max_len)

    if meta.get("apply_start_at") is not None and not _is_valid_date(meta.get("apply_start_at")):
        errors.append(f"{p_prefix}.apply_start_at must be YYYY-MM-DD or null")

    fresh_grad = meta.get("fresh_grad_only")
    if fresh_grad is not None and not isinstance(fresh_grad, bool):
        errors.append(f"{p_prefix}.fresh_grad_only must be a boolean or null")

    majors = meta.get("majors")
    if not isinstance(majors, list) or not all(isinstance(m, str) and 1 <= len(m.strip()) <= 100 for m in majors):
        errors.append(f"{p_prefix}.majors must be a list of non-empty strings")

    headcount = meta.get("headcount")
    if headcount is not None and (isinstance(headcount, bool) or not isinstance(headcount, int) or headcount < 1):
        errors.append(f"{p_prefix}.headcount must be a positive integer or null")

    attachments = meta.get("attachments")
    if not isinstance(attachments, list):
        errors.append(f"{p_prefix}.attachments must be a list")
    else:
        for a_idx, att in enumerate(attachments):
            a_prefix = f"{p_prefix}.attachments[{a_idx}]"
            if not isinstance(att, dict):
                errors.append(f"{a_prefix} must be an object")
                continue
            _check_allowed_keys(att, {"name", "url"}, {"file_type"}, a_prefix, errors)
            _check_non_empty_str(att, "name", a_prefix, errors, 200)
            if not _is_valid_uri(att.get("url")):
                errors.append(f"{a_prefix}.url must be a valid http/https URI")
            _check_optional_str(att, "file_type", a_prefix, errors, 20)


def _validate_enterprise_meta(
    meta: Any,
    prefix: str,
    known_company_ids: set[str],
    errors: list[str],
) -> None:
    if not isinstance(meta, dict):
        errors.append(f"{prefix}.enterprise_meta must be an object")
        return

    e_prefix = f"{prefix}.enterprise_meta"
    required = {"company_id", "work_schedule", "benefits_verified", "remote_policy"}
    optional = {"work_hours", "salary_range", "department", "experience_req", "education_req"}
    _check_allowed_keys(meta, required, optional, e_prefix, errors)

    cid = meta.get("company_id")
    if not isinstance(cid, str) or not cid.strip():
        errors.append(f"{e_prefix}.company_id must be a non-empty string")
    elif cid not in known_company_ids:
        errors.append(f"{e_prefix}.company_id ({cid!r}) does not exist in companies.json")

    _check_enum(meta, "work_schedule", JOB_SCHEDULES, e_prefix, errors)
    _check_enum(meta, "remote_policy", REMOTE_POLICIES, e_prefix, errors)

    for opt_key, max_len in (
        ("work_hours", 100),
        ("salary_range", 80),
        ("department", 100),
        ("experience_req", 80),
        ("education_req", 60),
    ):
        _check_optional_str(meta, opt_key, e_prefix, errors, max_len)

    benefits = meta.get("benefits_verified")
    if not isinstance(benefits, list):
        errors.append(f"{e_prefix}.benefits_verified must be a list")
    else:
        for b_idx, benefit in enumerate(benefits):
            b_prefix = f"{e_prefix}.benefits_verified[{b_idx}]"
            if not isinstance(benefit, dict):
                errors.append(f"{b_prefix} must be an object")
                continue
            _check_allowed_keys(benefit, {"tag", "evidence_quote"}, {"evidence_url"}, b_prefix, errors)
            _check_non_empty_str(benefit, "tag", b_prefix, errors, 40)
            _check_non_empty_str(benefit, "evidence_quote", b_prefix, errors, 300)
            if benefit.get("evidence_url") is not None and not _is_valid_uri(benefit.get("evidence_url")):
                errors.append(f"{b_prefix}.evidence_url must be a valid http/https URI or null")


def _validate_job(
    item: Any,
    idx: int,
    seen_ids: set[str],
    known_company_ids: set[str],
    errors: list[str],
) -> None:
    prefix = f"jobs.items[{idx}]"
    if not isinstance(item, dict):
        errors.append(f"{prefix} must be an object")
        return

    required = {
        "id",
        "category",
        "title",
        "org_name",
        "province",
        "city",
        "source_url",
        "source_name",
        "source_type",
        "status",
        "scraped_at",
        "last_seen_at",
        "content_hash",
        "summary",
        "tags",
    }
    optional = {"district", "apply_url", "published_at", "deadline_at", "public_meta", "enterprise_meta"}
    _check_allowed_keys(item, required, optional, prefix, errors)

    jid = item.get("id")
    if not isinstance(jid, str) or len(jid) < 4 or len(jid) > 128:
        errors.append(f"{prefix}.id must be a string between 4 and 128 chars")
    elif jid in seen_ids:
        errors.append(f"{prefix}.id ({jid!r}) is duplicated")
    else:
        seen_ids.add(jid)

    for key, max_len in (
        ("title", 200),
        ("org_name", 150),
        ("province", 40),
        ("city", 60),
        ("source_name", 120),
        ("content_hash", 128),
        ("summary", 500),
    ):
        _check_non_empty_str(item, key, prefix, errors, max_len)

    _check_enum(item, "category", JOB_CATEGORIES, prefix, errors)
    _check_enum(item, "source_type", SOURCE_TYPES, prefix, errors)
    _check_enum(item, "status", JOB_STATUSES, prefix, errors)

    if not _is_valid_uri(item.get("source_url")):
        errors.append(f"{prefix}.source_url must be a valid http/https URI")
    if item.get("apply_url") is not None and not _is_valid_uri(item.get("apply_url")):
        errors.append(f"{prefix}.apply_url must be a valid http/https URI or null")

    for date_key in ("published_at", "deadline_at"):
        if item.get(date_key) is not None and not _is_valid_date(item.get(date_key)):
            errors.append(f"{prefix}.{date_key} must be YYYY-MM-DD or null")

    for dt_key in ("scraped_at", "last_seen_at"):
        if not _is_valid_datetime(item.get(dt_key)):
            errors.append(f"{prefix}.{dt_key} must be ISO-8601 datetime")

    tags = item.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) and 1 <= len(t.strip()) <= 40 for t in tags):
        errors.append(f"{prefix}.tags must be a list of strings (1..40 chars)")

    category = item.get("category")
    if category == "public_institution":
        if item.get("enterprise_meta") is not None:
            errors.append(f"{prefix}.enterprise_meta must be null/omitted for public_institution")
        _validate_public_meta(item.get("public_meta"), prefix, errors)
    elif category == "enterprise":
        if item.get("public_meta") is not None:
            errors.append(f"{prefix}.public_meta must be null/omitted for enterprise")
        _validate_enterprise_meta(item.get("enterprise_meta"), prefix, known_company_ids, errors)


def _validate_sources(data: Any, errors: list[str]) -> None:
    if not isinstance(data, dict):
        errors.append("sources.json root must be an object")
        return
    _check_allowed_keys(data, {"schema_version", "sources"}, set(), "sources", errors)
    if data.get("schema_version") != "1.0":
        errors.append("sources.schema_version must be '1.0'")

    sources = data.get("sources")
    if not isinstance(sources, list):
        errors.append("sources.sources must be a list")
        return

    seen_ids: set[str] = set()
    for idx, src in enumerate(sources):
        prefix = f"sources.sources[{idx}]"
        if not isinstance(src, dict):
            errors.append(f"{prefix} must be an object")
            continue
        required = {"source_id", "name", "category", "province", "adapter", "base_url", "enabled", "url_verified"}
        optional = {"city", "list_urls", "fetch_interval_hours", "notes"}
        _check_allowed_keys(src, required, optional, prefix, errors)

        sid = src.get("source_id")
        if not isinstance(sid, str) or not SOURCE_ID_RE.match(sid):
            errors.append(f"{prefix}.source_id ({sid!r}) must match {SOURCE_ID_RE.pattern}")
        elif sid in seen_ids:
            errors.append(f"{prefix}.source_id ({sid!r}) is duplicated")
        else:
            seen_ids.add(sid)

        _check_non_empty_str(src, "name", prefix, errors, 120)
        _check_non_empty_str(src, "province", prefix, errors, 40)
        _check_non_empty_str(src, "adapter", prefix, errors, 60)
        _check_enum(src, "category", JOB_CATEGORIES, prefix, errors)
        if not _is_valid_uri(src.get("base_url")):
            errors.append(f"{prefix}.base_url must be a valid http/https URI")

        for b_key in ("enabled", "url_verified"):
            if not isinstance(src.get(b_key), bool):
                errors.append(f"{prefix}.{b_key} must be boolean")

        if src.get("enabled") and not src.get("url_verified"):
            errors.append(f"{prefix} cannot be enabled when url_verified is false")

        list_urls = src.get("list_urls", [])
        if not isinstance(list_urls, list) or not all(_is_valid_uri(u) for u in list_urls):
            errors.append(f"{prefix}.list_urls must be a list of valid http/https URIs")


def _load_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"Missing required file: {path}")
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid JSON in {path}: {exc}")
    return None


def validate_workspace(root_dir: Path = ROOT_DIR) -> list[str]:
    """Validate companies, jobs, and sources datasets under root_dir."""
    errors: list[str] = []

    companies_data = _load_json(root_dir / "data" / "companies.json", errors)
    jobs_data = _load_json(root_dir / "data" / "jobs.json", errors)
    sources_data = _load_json(root_dir / "config" / "sources.json", errors)

    known_company_ids: set[str] = set()
    if isinstance(companies_data, dict):
        _check_allowed_keys(companies_data, {"schema_version", "updated_at", "items"}, set(), "companies", errors)
        if companies_data.get("schema_version") != "1.0":
            errors.append("companies.schema_version must be '1.0'")
        if not _is_valid_datetime(companies_data.get("updated_at")):
            errors.append("companies.updated_at must be ISO-8601 datetime")
        items = companies_data.get("items")
        if not isinstance(items, list):
            errors.append("companies.items must be a list")
        else:
            for idx, item in enumerate(items):
                _validate_company(item, idx, known_company_ids, errors)
    elif companies_data is not None:
        errors.append("companies.json root must be an object")

    if isinstance(jobs_data, dict):
        _check_allowed_keys(jobs_data, {"schema_version", "updated_at", "items"}, set(), "jobs", errors)
        if jobs_data.get("schema_version") != "1.0":
            errors.append("jobs.schema_version must be '1.0'")
        if not _is_valid_datetime(jobs_data.get("updated_at")):
            errors.append("jobs.updated_at must be ISO-8601 datetime")
        items = jobs_data.get("items")
        if not isinstance(items, list):
            errors.append("jobs.items must be a list")
        else:
            seen_job_ids: set[str] = set()
            for idx, item in enumerate(items):
                _validate_job(item, idx, seen_job_ids, known_company_ids, errors)
    elif jobs_data is not None:
        errors.append("jobs.json root must be an object")

    if sources_data is not None:
        _validate_sources(sources_data, errors)

    return errors


def main() -> int:
    errors = validate_workspace(ROOT_DIR)
    if errors:
        print(f"[FAILED] {len(errors)} validation error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    jobs_count = len(json.loads((ROOT_DIR / "data" / "jobs.json").read_text(encoding="utf-8"))["items"])
    companies_count = len(json.loads((ROOT_DIR / "data" / "companies.json").read_text(encoding="utf-8"))["items"])
    sources_count = len(json.loads((ROOT_DIR / "config" / "sources.json").read_text(encoding="utf-8"))["sources"])
    print(f"[OK] Validation passed (jobs={jobs_count}, companies={companies_count}, sources={sources_count}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
