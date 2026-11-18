"""Standardized public recruitment SaaS & official JSON API adapters.

Supports zero-AI-token deterministic extraction into job.schema.json records for:
- Feishu Hire (*.jobs.feishu.cn public CSRF + /api/v1/search/job/posts)
- Greenhouse Boards API (boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true)
- Lever Postings API (api.lever.co/v0/postings/{site}?mode=json)
- Moka Recruitment Portal (embedded <input id="init-data"> JSON)
- EMQ Official Careers JSON API (community-sites.emqx.com/api/v1/jobs)
- Dify Official Careers JSON API (public-jobs Supabase endpoint)
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scrapers.core.base_adapter import BaseScraperAdapter, CrawlRunSummary
from scrapers.core.merger import merge_jobs_into_dataset
from scrapers.core.storage import TZ_CN, now_iso_cn

SOURCE_TO_COMPANY_ID: dict[str, str] = {
    "ent_songmics_feishu": "songmics-home",
    "ent_lilith_feishu": "lilith-games",
    "ent_moka_self": "moka-hr",
    "ent_trip_moka": "trip-com",
    "ent_canonical_greenhouse": "canonical-ubuntu",
    "ent_mongodb_greenhouse": "mongodb-inc",
    "ent_emq_jobs": "emq-technologies",
    "ent_dify_jobs": "dify-langgenius",
}

_PLATFORM_LABELS: dict[str, str] = {
    "feishu_hire": "飞书招聘",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "moka": "Moka招聘",
    "emq_jobs": "EMQ招聘官网",
    "dify_jobs": "Dify招聘官网",
}

_CITY_TO_PROVINCE: dict[str, str] = {
    "郑州市": "河南省",
    "洛阳市": "河南省",
    "许昌市": "河南省",
    "周口市": "河南省",
    "新乡市": "河南省",
    "安阳市": "河南省",
    "南阳市": "河南省",
    "开封市": "河南省",
    "北京市": "北京市",
    "上海市": "上海市",
    "天津市": "天津市",
    "重庆市": "重庆市",
    "深圳市": "广东省",
    "广州市": "广东省",
    "珠海市": "广东省",
    "东莞市": "广东省",
    "佛山市": "广东省",
    "杭州市": "浙江省",
    "苏州市": "江苏省",
    "南京市": "江苏省",
    "无锡市": "江苏省",
    "成都市": "四川省",
    "武汉市": "湖北省",
    "厦门市": "福建省",
    "福州市": "福建省",
    "昆明市": "云南省",
    "西安市": "陕西省",
    "长沙市": "湖南省",
    "合肥市": "安徽省",
    "南昌市": "江西省",
    "济南市": "山东省",
    "青岛市": "山东省",
    "大连市": "辽宁省",
    "沈阳市": "辽宁省",
    "远程": "全国/远程",
}

_CITY_ALIAS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("郑州市", ("郑州", "zhengzhou")),
    ("洛阳市", ("洛阳", "luoyang")),
    ("许昌市", ("许昌", "xuchang")),
    ("周口市", ("周口", "zhoukou")),
    ("新乡市", ("新乡", "xinxiang")),
    ("北京市", ("北京", "beijing")),
    ("上海市", ("上海", "shanghai")),
    ("深圳市", ("深圳", "shenzhen")),
    ("广州市", ("广州", "guangzhou")),
    ("珠海市", ("珠海", "zhuhai")),
    ("东莞市", ("东莞", "dongguan")),
    ("杭州市", ("杭州", "hangzhou")),
    ("苏州市", ("苏州", "suzhou")),
    ("南京市", ("南京", "nanjing")),
    ("成都市", ("成都", "chengdu")),
    ("武汉市", ("武汉", "wuhan")),
    ("厦门市", ("厦门", "xiamen")),
    ("昆明市", ("昆明", "kunming")),
    ("西安市", ("西安", "xian", "xi'an")),
    ("长沙市", ("长沙", "changsha")),
    ("重庆市", ("重庆", "chongqing")),
    ("天津市", ("天津", "tianjin")),
    ("合肥市", ("合肥", "hefei")),
)

_GB_CITY_PREFIX_MAP: dict[str, str] = {
    "11": "北京市",
    "12": "天津市",
    "31": "上海市",
    "50": "重庆市",
    "4101": "郑州市",
    "4103": "洛阳市",
    "4110": "许昌市",
    "4116": "周口市",
    "4401": "广州市",
    "4403": "深圳市",
    "4404": "珠海市",
    "4419": "东莞市",
    "3301": "杭州市",
    "3201": "南京市",
    "3205": "苏州市",
    "5101": "成都市",
    "4201": "武汉市",
    "3502": "厦门市",
    "5301": "昆明市",
    "6101": "西安市",
    "4301": "长沙市",
    "3401": "合肥市",
}

_NON_MAINLAND_EXCLUDE_RE = re.compile(
    r"\b(japan|japanese|tokyo|osaka|india|bangalore|bengaluru|korea|korean|seoul|"
    r"australia|sydney|melbourne|anz|new zealand|singapore|indonesia|jakarta|thailand|"
    r"bangkok|vietnam|malaysia|kuala lumpur|philippines|manila|united states|usa|"
    r"canada|toronto|emea|europe|uk|london|germany|berlin|munich|france|paris|"
    r"spain|brazil|latam)\b",
    re.I,
)
_CHINA_OR_GLOBAL_REMOTE_RE = re.compile(
    r"(china|beijing|shanghai|shenzhen|hangzhou|guangzhou|chengdu|zhengzhou|suzhou|henan|"
    r"mandarin|中国|北京|上海|深圳|杭州|广州|成都|郑州|苏州|河南|globally remote|remote globally|"
    r"home based - apac|远程)",
    re.I,
)
_SALARY_RE = re.compile(r"薪资(?:范围|待遇)?[：:\s]*([^\n\r。；;]{3,50})")
_WORK_HOURS_RE = re.compile(r"(?:工作时间|上班时间)[：:\s]*([^\n\r。；;]{4,60})")

_JD_BENEFIT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("周末双休", re.compile(r"(周末双休|双休)")),
    ("弹性工作", re.compile(r"(弹性工作|弹性办公|弹性打卡|flexible working|remote-first)", re.I)),
    ("混合办公", re.compile(r"(混合办公|混合工作政策|hybrid working model|hybrid work|hybrid role)", re.I)),
    ("不打卡", re.compile(r"(拒绝无意义的打卡|不打卡)")),
    ("补充医疗", re.compile(r"(补充医疗|补充商业保险|补充商业医疗|商业保险|medical insurance|health and vision)", re.I)),
    ("年度体检", re.compile(r"(年度免费体检|年度体检|全面体检|免费体检)")),
    ("带薪年假", re.compile(r"(带薪年假|年休假|全薪病假|annual holiday leave|generous parental leave|paid time off)", re.I)),
    ("学习发展预算", re.compile(r"(learning and development budget|语言学习补贴|课程学习)", re.I)),
)


def strip_html_text(raw_html: str) -> str:
    """Convert HTML snippet to clean plain text."""
    if not raw_html:
        return ""
    unescaped = html_lib.unescape(str(raw_html))
    if "<" in unescaped and ">" in unescaped:
        soup = BeautifulSoup(unescaped, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
    else:
        text = unescaped
    lines = [re.sub(r"[ \t\u00a0\u3000]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def normalize_cn_city(
    *hints: Any,
    city_id: int | str | None = None,
    default_city: str = "多城市",
) -> tuple[str, str]:
    """Normalize location hints and optional GB city_id into (province, city)."""
    if city_id is not None:
        code_str = re.sub(r"\D", "", str(city_id))
        if len(code_str) >= 4 and code_str[:4] in _GB_CITY_PREFIX_MAP:
            std_city = _GB_CITY_PREFIX_MAP[code_str[:4]]
            return _CITY_TO_PROVINCE.get(std_city, "中国"), std_city
        if len(code_str) >= 2 and code_str[:2] in _GB_CITY_PREFIX_MAP:
            std_city = _GB_CITY_PREFIX_MAP[code_str[:2]]
            return _CITY_TO_PROVINCE.get(std_city, "中国"), std_city

    joined = " ".join(str(h) for h in hints if h).strip()
    lower = joined.lower()

    # Check explicit city aliases (when city_id is absent or unmapped)
    for std_city, aliases in _CITY_ALIAS_PATTERNS:
        if any(alias in lower for alias in aliases):
            return _CITY_TO_PROVINCE.get(std_city, "中国"), std_city

    if any(tok in lower for tok in ("远程", "remote", "home based", "distributed", "anywhere")):
        return "全国/远程", "远程"

    return _CITY_TO_PROVINCE.get(default_city, "中国"), default_city


def parse_date_value(value: Any) -> str | None:
    """Parse ISO-8601 string or epoch seconds/milliseconds into YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e11:  # milliseconds
            ts /= 1000.0
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=TZ_CN).strftime("%Y-%m-%d")
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{10,13}", raw):
        return parse_date_value(int(raw))
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    return None


def _clip(text: str, max_len: int) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max(0, max_len - 1)].rstrip() + "…"


def _extract_jd_signals(
    plain_text: str,
    source_url: str,
    company_profile: dict[str, Any],
) -> tuple[str, str | None, str | None, list[dict[str, Any]]]:
    """Extract verified work_schedule, work_hours, salary_range, and benefits_verified from JD + company claims."""
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", plain_text) if ln.strip()]
    benefits: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for line in lines:
        if len(line) < 4:
            continue
        for tag, pat in _JD_BENEFIT_RULES:
            if pat.search(line):
                quote = _clip(line, 280)
                key = (tag, quote)
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    benefits.append(
                        {
                            "tag": tag[:40],
                            "evidence_quote": quote,
                            "evidence_url": source_url,
                        }
                    )
                if len(benefits) >= 4:
                    break
        if len(benefits) >= 4:
            break

    # Supplement with verified company-level benefit claims (always grounded in companies.json)
    for claim in company_profile.get("benefit_claims") or []:
        if len(benefits) >= 5:
            break
        tag = str(claim.get("label") or "").strip()[:40]
        quote = str(claim.get("evidence_quote") or "").strip()
        ev_url = str(claim.get("evidence_url") or "").strip()
        if not tag or len(quote) < 2:
            continue
        key = (tag, quote)
        if key not in seen_pairs and not any(b["tag"] == tag for b in benefits):
            seen_pairs.add(key)
            benefits.append(
                {
                    "tag": tag,
                    "evidence_quote": _clip(quote, 295),
                    "evidence_url": ev_url or source_url,
                }
            )

    work_hours: str | None = None
    m_hours = _WORK_HOURS_RE.search(plain_text)
    if m_hours:
        work_hours = _clip(m_hours.group(1), 100)

    salary_range: str | None = None
    m_sal = _SALARY_RE.search(plain_text)
    if m_sal:
        salary_range = _clip(m_sal.group(1), 80)

    comp_sched = str(company_profile.get("work_schedule") or "其他")
    if "周末双休" in plain_text or "双休" in plain_text:
        job_schedule = "周末双休"
    elif comp_sched in {"周末双休", "弹性工作", "做四休三"}:
        job_schedule = comp_sched
    elif any(k in plain_text.lower() for k in ("弹性工作", "弹性办公", "不打卡", "remote-first", "flexible working")):
        job_schedule = "弹性工作"
    else:
        job_schedule = "未明确"

    return job_schedule, work_hours, salary_range, benefits


def _infer_remote_policy(location_text: str, body_text: str) -> str:
    loc_l = (location_text or "").lower()
    body_l = (body_text or "").lower()
    has_loc_remote = any(k in loc_l for k in ("home based", "remote", "远程", "distributed"))
    has_loc_office = any(k in loc_l for k in ("office based", "office-based", "onsite", "线下"))

    if has_loc_remote and has_loc_office:
        return "混合办公"
    if has_loc_remote:
        return "可远程"
    if has_loc_office or any(k in body_l[:500] for k in ("office-based role", "office based", "线下实习", "全职到岗", "全日制到岗")):
        return "线下办公"
    if loc_l.strip() == "hybrid" or any(
        k in loc_l + " " + body_l[:1500]
        for k in ("混合办公", "混合工作", "hybrid working", "hybrid model", "hybrid role")
    ):
        return "混合办公"
    if any(k in loc_l + " " + body_l[:1200] for k in ("远程办公", "remote-first", "globally remote", "home based - apac")):
        return "可远程"
    return "未明确"


def _make_job_id(company_id: str, raw_id: str) -> str:
    safe_raw = re.sub(r"[^a-zA-Z0-9_-]", "-", str(raw_id).strip()).strip("-")
    candidate = f"ent-{company_id}-{safe_raw}".lower()
    if len(candidate) <= 120 and len(candidate) >= 4:
        return candidate
    digest = hashlib.sha256(f"{company_id}:{raw_id}".encode("utf-8")).hexdigest()[:16]
    return f"ent-{company_id[:40]}-{digest}"


def _is_china_eligible_global_role(title: str, location: str, body: str) -> bool:
    """Filter global ATS boards (Greenhouse/Lever) for roles open to Mainland China candidates."""
    title_loc = f"{title} | {location}"
    if re.search(r"(china|beijing|shanghai|shenzhen|hangzhou|guangzhou|chengdu|zhengzhou|suzhou|mandarin|中国|北京|上海|深圳|杭州|郑州)", title_loc, re.I):
        # Skip Taiwan-only when Beijing/China/Remote is absent
        if "taipei" in title_loc.lower() and not re.search(r"(beijing|shanghai|shenzhen|china|apac|remote)", title_loc, re.I):
            return False
        return True

    # Remote / APAC roles: exclude country-locked non-China postings
    if "home based - apac" in location.lower() or "globally remote" in body[:800].lower() or "remote globally" in body[:800].lower():
        if _NON_MAINLAND_EXCLUDE_RE.search(title_loc) or _NON_MAINLAND_EXCLUDE_RE.search(body[:600]):
            return False
        if "home based - apac" in location.lower() or "globally remote" in body[:800].lower():
            return True

    return False


class SaasBaseAdapter(BaseScraperAdapter):
    """Base adapter for standardized SaaS recruitment platforms (zero AI tokens)."""

    adapter_name: str = "saas_base"
    source_type: str = "verified_saas"

    def _resolve_company_profile(self) -> dict[str, Any]:
        """Look up verified company metadata from data/companies.json or source_config."""
        explicit = self.source_config.get("_company_profile")
        if isinstance(explicit, dict):
            return explicit

        companies_path = self.state_store.root_dir / "data" / "companies.json"
        target_cid = str(
            self.source_config.get("company_id")
            or SOURCE_TO_COMPANY_ID.get(self.source_id, "")
        ).strip()

        if companies_path.exists():
            try:
                data = json.loads(companies_path.read_text(encoding="utf-8"))
                items = data.get("items") or []
                for comp in items:
                    if not isinstance(comp, dict):
                        continue
                    if target_cid and comp.get("company_id") == target_cid:
                        return comp
                # Fallback domain matching
                base_host = urlparse(self.base_url).netloc.lower()
                for comp in items:
                    if not isinstance(comp, dict):
                        continue
                    c_host = urlparse(str(comp.get("career_url") or "")).netloc.lower()
                    if base_host and c_host and base_host == c_host and base_host not in {"app.mokahr.com", "boards-api.greenhouse.io", "api.lever.co"}:
                        return comp
            except (OSError, json.JSONDecodeError):
                pass

        return {
            "company_id": target_cid or re.sub(r"[^a-z0-9-]", "-", self.source_id.lower()).strip("-"),
            "name": self.source_name.split("（", 1)[0].strip() or self.source_name,
            "cities": [str(self.source_config.get("city") or "多城市")],
            "work_schedule": "其他",
            "benefit_claims": [],
        }

    def _build_job_record(
        self,
        *,
        raw_id: str,
        title: str,
        source_url: str,
        apply_url: str | None,
        location_text: str,
        body_text: str,
        published_at: str | None = None,
        department: str | None = None,
        city_id: int | str | None = None,
        education_req: str | None = None,
        experience_req: str | None = None,
        extra_tags: list[str] | None = None,
        company_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        company = company_profile or self._resolve_company_profile()
        company_id = str(company["company_id"])
        org_name = str(company["name"])
        default_city = str((company.get("cities") or ["多城市"])[0])

        province, city = normalize_cn_city(location_text, title, city_id=city_id, default_city=default_city)
        job_schedule, work_hours, salary_range, benefits_verified = _extract_jd_signals(
            body_text,
            source_url,
            company,
        )
        remote_policy = _infer_remote_policy(location_text, body_text)

        summary_src = strip_html_text(body_text) or f"{org_name} 官网公开在招岗位（{city}）。"
        summary = _clip(summary_src, 420)
        if not summary:
            summary = f"{org_name} 官网公开在招岗位（{city}）。"

        platform_label = _PLATFORM_LABELS.get(self.adapter_name, "官网直招")
        source_name = _clip(f"{org_name}({platform_label})", 120)

        tag_candidates = ["高福利民企", city, job_schedule]
        if remote_policy in {"可远程", "混合办公"}:
            tag_candidates.append(remote_policy)
        for b in benefits_verified[:3]:
            tag_candidates.append(str(b["tag"]))
        if extra_tags:
            tag_candidates.extend(extra_tags)

        tags: list[str] = []
        for t in tag_candidates:
            cleaned_t = t.strip()[:40]
            if cleaned_t and cleaned_t not in tags and cleaned_t != "未明确":
                tags.append(cleaned_t)

        ts = now_iso_cn()
        digest_payload = json.dumps(
            {
                "id": raw_id,
                "title": title,
                "city": city,
                "url": source_url,
                "dept": department,
                "body": summary[:200],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        content_hash = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()

        return {
            "id": _make_job_id(company_id, raw_id),
            "category": "enterprise",
            "title": _clip(title, 200),
            "org_name": _clip(org_name, 150),
            "province": _clip(province, 40),
            "city": _clip(city, 60),
            "source_url": source_url,
            "apply_url": apply_url,
            "source_name": source_name,
            "source_type": self.source_type,
            "published_at": published_at,
            "deadline_at": None,
            "status": "active",
            "scraped_at": ts,
            "last_seen_at": ts,
            "content_hash": content_hash,
            "summary": summary,
            "tags": tags[:10],
            "enterprise_meta": {
                "company_id": company_id,
                "work_schedule": job_schedule,
                "work_hours": work_hours,
                "salary_range": salary_range,
                "remote_policy": remote_policy,
                "department": _clip(department, 100) if department else None,
                "experience_req": _clip(experience_req, 80) if experience_req else None,
                "education_req": _clip(education_req, 60) if education_req else None,
                "benefits_verified": benefits_verified,
            },
        }

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        """Fetch and transform active enterprise jobs from SaaS endpoints."""
        raise NotImplementedError

    def run(
        self,
        *,
        dry_run: bool = False,
        max_items: int = 12,
        fetch_attachments: bool = False,
        probe_base_when_empty: bool = True,
    ) -> CrawlRunSummary:
        """Fetch standardized SaaS JSON jobs and merge directly into data/jobs.json (zero AI tokens)."""
        del fetch_attachments, probe_base_when_empty
        summary = CrawlRunSummary(
            source_id=self.source_id,
            source_name=self.source_name,
            dry_run=dry_run,
        )
        try:
            jobs = self.fetch_jobs(max_items=max_items)
            summary.list_urls_scanned = max(1, len(self.list_urls))
            summary.announcements_fetched = len(jobs)
        except Exception as exc:
            summary.errors.append(f"SaaS fetch failed ({self.source_id}): {exc}")
            return summary

        if dry_run:
            for job in jobs:
                st = self.state_store.check_status(job["source_url"], job["content_hash"])
                if st == "new":
                    summary.new_count += 1
                elif st == "changed":
                    summary.changed_count += 1
                else:
                    summary.unchanged_count += 1
            return summary

        if jobs:
            merge_res = merge_jobs_into_dataset(
                jobs,
                root_dir=self.state_store.root_dir,
                archive_expired=False,
                dry_run=False,
            )
            summary.new_count = merge_res.inserted
            summary.changed_count = merge_res.updated
            summary.unchanged_count = merge_res.unchanged

            ts = now_iso_cn()
            for job in jobs:
                self.state_store.records[job["source_url"]] = {
                    "source_id": self.source_id,
                    "url": job["source_url"],
                    "title": job["title"],
                    "published_at": job.get("published_at"),
                    "content_hash": job["content_hash"],
                    "first_seen_at": self.state_store.records.get(job["source_url"], {}).get("first_seen_at", ts),
                    "last_seen_at": ts,
                    "inbox_file": "data/jobs.json",
                }
            self.state_store.save_state()

        return summary


class FeishuHireAdapter(SaasBaseAdapter):
    """Adapter for Feishu Hire (*.jobs.feishu.cn) public CSRF + search/job/posts JSON API."""

    adapter_name = "feishu_hire"

    @staticmethod
    def extract_website_path(portal_html: str, portal_url: str) -> str:
        m = re.search(r'<script[^>]*id="js-websiteInfo"[^>]*>(.*?)</script>', portal_html or "", re.S)
        if m:
            try:
                info = json.loads(m.group(1))
                wpath = (info.get("website_info") or {}).get("path")
                if isinstance(wpath, str) and wpath.strip():
                    return wpath.strip()
            except json.JSONDecodeError:
                pass
        path_seg = urlparse(portal_url).path.strip("/").split("/")[0]
        return path_seg or "index"

    def parse_posts_payload(
        self,
        posts: list[dict[str, Any]],
        *,
        portal_origin: str,
        website_path: str,
        preferred_city: str | None = None,
        max_items: int = 12,
    ) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        candidates: list[tuple[int, dict[str, Any]]] = []

        for item in posts:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not jid or not title:
                continue

            city_names = [
                str(c.get("name")).strip()
                for c in (item.get("city_list") or [])
                if isinstance(c, dict) and c.get("name")
            ]
            city_info_name = str((item.get("city_info") or {}).get("name") or "").strip()
            if city_info_name and city_info_name not in city_names:
                city_names.insert(0, city_info_name)

            loc_text = ", ".join(city_names)
            desc = str(item.get("description") or "").strip()
            req = str(item.get("requirement") or "").strip()
            full_body = "\n".join(part for part in (desc, req) if part)

            detail_url = f"{portal_origin.rstrip('/')}/{website_path}/position/{jid}/detail"
            dept = (item.get("department_info") or {}).get("name") or (item.get("job_category") or {}).get("name")
            pub_date = parse_date_value(item.get("publish_time"))

            pref_rank = 0
            if preferred_city and preferred_city not in {"多城市", "全省"}:
                short_pref = preferred_city.rstrip("市")
                if any(short_pref in cn for cn in city_names):
                    pref_rank = 2
                elif any(x in loc_text for x in ("远程", "全国")):
                    pref_rank = 1

            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=loc_text,
                body_text=full_body,
                published_at=pub_date,
                department=str(dept).strip() if dept else None,
                company_profile=company,
            )
            candidates.append((pref_rank, rec))

        # Keep preferred city first while preserving recency order within tier
        candidates.sort(key=lambda pair: -pair[0])
        return [rec for _, rec in candidates[: max(0, max_items)]]

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        preferred_city = str(self.source_config.get("city") or "").strip() or None
        collected_posts: list[dict[str, Any]] = []
        portal_origin = self.base_url.rstrip("/")
        wpath = "index"

        for portal_url in target_urls:
            parsed = urlparse(portal_url)
            portal_origin = f"{parsed.scheme}://{parsed.netloc}"
            page_resp = self.http_client.get(portal_url)
            wpath = self.extract_website_path(page_resp.text, page_resp.url)

            csrf_resp = self.http_client.request(
                "POST",
                f"{portal_origin}/api/v1/csrf/token",
                headers={"Referer": page_resp.url, "Origin": portal_origin},
            )
            csrf_token = ((json.loads(csrf_resp.text).get("data") or {}).get("token") or "").strip()

            api_headers: dict[str, str] = {
                "Referer": page_resp.url,
                "Origin": portal_origin,
                "Content-Type": "application/json",
                "Website-Path": wpath,
            }
            if csrf_token:
                api_headers["x-csrf-token"] = csrf_token

            # Scan up to 80 latest posts so city filtering (e.g. Zhengzhou) has plenty of candidates
            for offset in range(0, 80, 20):
                post_resp = self.http_client.request(
                    "POST",
                    f"{portal_origin}/api/v1/search/job/posts",
                    headers=api_headers,
                    json_body={
                        "limit": 20,
                        "offset": offset,
                        "keyword": "",
                        "job_category_id_list": [],
                        "location_code_list": [],
                        "portal_type": 2,
                        "portal_entrance": 1,
                    },
                )
                payload = json.loads(post_resp.text)
                if payload.get("code") != 0:
                    raise RuntimeError(f"Feishu Hire API error: {payload.get('message')}")
                batch = (payload.get("data") or {}).get("job_post_list") or []
                if not batch:
                    break
                collected_posts.extend(batch)
                if len(batch) < 20:
                    break

        return self.parse_posts_payload(
            collected_posts,
            portal_origin=portal_origin,
            website_path=wpath,
            preferred_city=preferred_city,
            max_items=max_items,
        )


class GreenhouseAdapter(SaasBaseAdapter):
    """Adapter for Greenhouse Boards public JSON API (/v1/boards/{token}/jobs?content=true)."""

    adapter_name = "greenhouse"

    @staticmethod
    def _normalize_api_url(url: str) -> str:
        if "boards-api.greenhouse.io" in url:
            return url
        parsed = urlparse(url)
        slug = parsed.path.strip("/").split("/")[0]
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def parse_jobs_payload(self, payload: dict[str, Any], *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        raw_jobs = payload.get("jobs") or []
        scored: list[tuple[int, dict[str, Any]]] = []

        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            abs_url = str(item.get("absolute_url") or "").strip()
            if not jid or not title or not abs_url:
                continue

            loc_name = str((item.get("location") or {}).get("name") or "").strip()
            plain_body = strip_html_text(str(item.get("content") or ""))
            if not _is_china_eligible_global_role(title, loc_name, plain_body):
                continue

            depts = [str(d.get("name")).strip() for d in (item.get("departments") or []) if isinstance(d, dict) and d.get("name")]
            dept = depts[0] if depts else None
            pub_date = parse_date_value(item.get("updated_at") or item.get("first_published"))

            # Prefer explicit China city / Mandarin roles over broad APAC-remote
            priority = 2 if re.search(r"(china|beijing|shanghai|shenzhen|hangzhou|zhengzhou|mandarin)", f"{title} {loc_name}", re.I) else 1
            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=abs_url,
                apply_url=abs_url,
                location_text=loc_name,
                body_text=plain_body,
                published_at=pub_date,
                department=dept,
                company_profile=company,
            )
            scored.append((priority, rec))

        scored.sort(key=lambda pair: (pair[0], pair[1].get("published_at") or ""), reverse=True)
        return [rec for _, rec in scored[: max(0, max_items)]]

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = [self._normalize_api_url(u) for u in (self.list_urls or [self.base_url])]
        merged_raw: list[dict[str, Any]] = []
        for api_url in target_urls:
            resp = self.http_client.get(api_url)
            payload = json.loads(resp.text)
            merged_raw.extend(payload.get("jobs") or [])
        return self.parse_jobs_payload({"jobs": merged_raw}, max_items=max_items)


class LeverAdapter(SaasBaseAdapter):
    """Adapter for Lever Postings public JSON API (api.lever.co/v0/postings/{site}?mode=json)."""

    adapter_name = "lever"

    def parse_postings_payload(self, postings: list[dict[str, Any]], *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        results: list[dict[str, Any]] = []

        for item in postings:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("text") or "").strip()
            hosted_url = str(item.get("hostedUrl") or "").strip()
            if not jid or not title or not hosted_url:
                continue

            cats = item.get("categories") or {}
            loc_name = str(cats.get("location") or "").strip()
            workplace = str(item.get("workplaceType") or "").strip()
            loc_combined = f"{loc_name} {workplace}".strip()

            list_texts = []
            for lst in item.get("lists") or []:
                if isinstance(lst, dict):
                    hdr = str(lst.get("text") or "").strip()
                    cnt = strip_html_text(str(lst.get("content") or ""))
                    if hdr or cnt:
                        list_texts.append(f"{hdr}\n{cnt}".strip())

            plain_body = "\n".join(
                part
                for part in (
                    str(item.get("descriptionPlain") or "").strip(),
                    "\n".join(list_texts),
                    str(item.get("additionalPlain") or "").strip(),
                )
                if part
            )

            if not _is_china_eligible_global_role(title, loc_combined, plain_body):
                continue

            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=hosted_url,
                apply_url=str(item.get("applyUrl") or hosted_url),
                location_text=loc_combined,
                body_text=plain_body,
                published_at=parse_date_value(item.get("createdAt")),
                department=str(cats.get("department") or cats.get("team") or "").strip() or None,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        all_postings: list[dict[str, Any]] = []
        for url in target_urls:
            resp = self.http_client.get(url)
            data = json.loads(resp.text)
            if isinstance(data, list):
                all_postings.extend(data)
        return self.parse_postings_payload(all_postings, max_items=max_items)


class MokaAdapter(SaasBaseAdapter):
    """Adapter for Moka public recruitment portals via embedded init-data JSON."""

    adapter_name = "moka"

    @staticmethod
    def extract_init_data(html_text: str) -> dict[str, Any]:
        m = re.search(r'<input[^>]*id="init-data"[^>]*value="([^"]*)"', html_text or "")
        if not m:
            raise ValueError("Moka init-data hidden input not found in portal HTML")
        return json.loads(html_lib.unescape(m.group(1)))

    def parse_portal_html(self, html_text: str, portal_url: str, *, max_items: int = 12) -> list[dict[str, Any]]:
        data = self.extract_init_data(html_text)
        company = self._resolve_company_profile()
        raw_jobs = data.get("jobs") or []
        base_portal = portal_url.split("#", 1)[0].rstrip("/")

        results: list[dict[str, Any]] = []
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            if status and status != "open":
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not jid or not title:
                continue

            loc_obj = item.get("location") or {}
            if not loc_obj and isinstance(item.get("locations"), list) and item["locations"]:
                loc_obj = item["locations"][0] or {}
            address = str(loc_obj.get("address") or "").strip()
            city_id = loc_obj.get("cityId")
            country = str(loc_obj.get("country") or "").strip()
            if country and country not in {"中国", "China"}:
                continue

            dept_obj = item.get("department") or {}
            dept = str(dept_obj.get("name") or "").strip() or None
            zhineng = str((item.get("zhineng") or {}).get("name") or "").strip() or None
            pub_date = parse_date_value(item.get("publishedAt") or item.get("openedAt") or item.get("updatedAt"))

            detail_url = f"{base_portal}#/job/{jid}"
            body_parts = [f"官方在招职位：{title}"]
            if dept:
                body_parts.append(f"所属部门：{dept}")
            elif zhineng and zhineng != "其它":
                body_parts.append(f"职能类别：{zhineng}")
            if address:
                body_parts.append(f"办公地点：{address}")
            if item.get("commitment"):
                body_parts.append(f"职位性质：{item['commitment']}")

            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=address,
                body_text="\n".join(body_parts),
                published_at=pub_date,
                department=dept or (zhineng if zhineng and zhineng != "其它" else None),
                city_id=city_id,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        all_jobs: list[dict[str, Any]] = []
        for url in target_urls:
            resp = self.http_client.get(url)
            all_jobs.extend(self.parse_portal_html(resp.text, resp.url, max_items=max_items))
        return all_jobs[: max(0, max_items)]


class EmqJobsAdapter(SaasBaseAdapter):
    """Adapter for EMQ careers public JSON API (community-sites.emqx.com/api/v1/jobs)."""

    adapter_name = "emq_jobs"
    source_type = "company_official"

    def parse_api_payload(self, payload: dict[str, Any], *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        items = (payload.get("data") or {}).get("items") or []
        results: list[dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("enable") not in (1, True, None):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("titleCN") or item.get("titleEN") or "").strip()
            if not jid or not title:
                continue

            work_place = str(item.get("workPlaceCN") or item.get("workPlaceEN") or "").strip()
            body = strip_html_text(str(item.get("contentCN") or item.get("contentEN") or ""))
            detail_url = f"{self.base_url.rstrip('/')}/zh/job?id={jid}"
            pub_date = parse_date_value(item.get("updateAt") or item.get("createAt"))

            extra_tags = [p.strip() for p in re.split(r"[,，/、]", work_place) if p.strip()][:3]
            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=work_place,
                body_text=body,
                published_at=pub_date,
                extra_tags=extra_tags,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        all_items: list[dict[str, Any]] = []
        for url in target_urls:
            resp = self.http_client.get(url)
            payload = json.loads(resp.text)
            all_items.extend((payload.get("data") or {}).get("items") or [])
        return self.parse_api_payload({"data": {"items": all_items}}, max_items=max_items)


class DifyJobsAdapter(SaasBaseAdapter):
    """Adapter for Dify official careers JSON endpoint (join.dify.ai)."""

    adapter_name = "dify_jobs"
    source_type = "company_official"

    def parse_api_payload(self, payload: dict[str, Any], *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        jobs = payload.get("jobs") or []
        results: list[dict[str, Any]] = []

        for item in jobs:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not jid or not title:
                continue

            location = str(item.get("location") or "").strip()
            work_site = str(item.get("work_site") or "").strip()
            loc_combined = " ".join(part for part in (work_site, location) if part)
            if not _CHINA_OR_GLOBAL_REMOTE_RE.search(loc_combined):
                continue

            content_text = strip_html_text(str(item.get("content") or ""))
            benefits_text = strip_html_text(str(item.get("benefits") or ""))
            full_body = "\n".join(part for part in (benefits_text, content_text) if part)
            detail_url = f"{self.base_url.rstrip('/')}/roles#{jid}"

            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=detail_url,
                apply_url=urljoin(self.base_url.rstrip("/") + "/", "roles"),
                location_text=loc_combined,
                body_text=full_body,
                published_at=parse_date_value(item.get("updated_at") or item.get("created_at")),
                department=str(item.get("department") or "").strip() or None,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        all_jobs: list[dict[str, Any]] = []
        for url in target_urls:
            resp = self.http_client.get(url)
            payload = json.loads(resp.text)
            all_jobs.extend(payload.get("jobs") or [])
        return self.parse_api_payload({"jobs": all_jobs}, max_items=max_items)
