"""Safely merge structured job records into data/jobs.json with fingerprint deduplication,
automatic deadline expiration, and monthly archiving to data/archive/YYYY-MM.json.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from scrapers.core.storage import TZ_CN, now_iso_cn
from scripts.validate_data import ROOT_DIR, _validate_job

_YM_RE = re.compile(r"^\d{4}-\d{2}")


@dataclass(slots=True)
class MergeSummary:
    """Summary of merging structured jobs into data/jobs.json."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    expired_marked: int = 0
    archived: int = 0
    active_total: int = 0
    archive_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ArchiveSummary:
    """Summary of expiring and archiving jobs by month."""

    expired_marked: int = 0
    archived_count: int = 0
    remaining_active: int = 0
    archive_files: list[str] = field(default_factory=list)


def _norm_token(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def compute_job_fingerprint(job: dict[str, Any]) -> str:
    """Compute a logical deduplication fingerprint for a job item."""
    category = _norm_token(job.get("category"))
    source_url = _norm_token(job.get("source_url"))
    org_name = _norm_token(job.get("org_name"))
    title = _norm_token(job.get("title"))
    city = _norm_token(job.get("city"))

    if category == "public_institution":
        pub_meta = job.get("public_meta") or {}
        post_code = _norm_token(pub_meta.get("post_code"))
        dept = _norm_token(pub_meta.get("department"))
        key = f"pub|{source_url}|{org_name}|{dept}|{post_code}|{title}|{city}"
    else:
        ent_meta = job.get("enterprise_meta") or {}
        company_id = _norm_token(ent_meta.get("company_id")) or org_name
        dept = _norm_token(ent_meta.get("department"))
        key = f"ent|{company_id}|{dept}|{title}|{city}|{source_url}"

    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def expire_outdated_jobs(jobs: list[dict[str, Any]], today: date | None = None) -> int:
    """Mark jobs with deadline_at < today as 'expired'. Returns count of jobs newly marked expired."""
    ref_date = today or datetime.now(TZ_CN).date()
    expired_count = 0

    for item in jobs:
        deadline_str = item.get("deadline_at")
        if not isinstance(deadline_str, str) or not deadline_str.strip():
            continue
        try:
            deadline_date = date.fromisoformat(deadline_str.strip())
        except ValueError:
            continue

        if deadline_date < ref_date and item.get("status") == "active":
            item["status"] = "expired"
            expired_count += 1

    return expired_count


def _determine_archive_month(job: dict[str, Any]) -> str:
    """Determine YYYY-MM bucket for an expired/closed job."""
    for key in ("deadline_at", "published_at", "scraped_at", "last_seen_at"):
        val = job.get(key)
        if isinstance(val, str) and _YM_RE.match(val.strip()):
            return val.strip()[:7]
    return datetime.now(TZ_CN).strftime("%Y-%m")


def _load_dataset(path: Path) -> dict[str, Any]:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    return {
        "schema_version": "1.0",
        "updated_at": now_iso_cn(),
        "items": [],
    }


def _save_dataset(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "updated_at": now_iso_cn(),
        "items": items,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_known_company_ids(root_dir: Path) -> set[str]:
    companies_path = root_dir / "data" / "companies.json"
    if not companies_path.exists():
        return set()
    data = json.loads(companies_path.read_text(encoding="utf-8"))
    items = data.get("items", []) if isinstance(data, dict) else []
    return {
        str(c["company_id"])
        for c in items
        if isinstance(c, dict) and isinstance(c.get("company_id"), str)
    }


def _validate_jobs_batch(jobs: list[dict[str, Any]], known_company_ids: set[str]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(jobs):
        _validate_job(item, idx, seen_ids, known_company_ids, errors)
    return errors


def _merge_item_lists(
    existing_items: list[dict[str, Any]],
    incoming_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Merge incoming_items into existing_items by id or logical fingerprint.

    Returns (merged_list, inserted_count, updated_count, unchanged_count).
    """
    merged: list[dict[str, Any]] = [copy.deepcopy(it) for it in existing_items]
    id_index: dict[str, int] = {}
    fp_index: dict[str, int] = {}

    for idx, item in enumerate(merged):
        jid = str(item.get("id", ""))
        if jid:
            id_index[jid] = idx
        fp_index[compute_job_fingerprint(item)] = idx

    inserted = 0
    updated = 0
    unchanged = 0

    for raw_in in incoming_items:
        candidate = copy.deepcopy(raw_in)
        cand_id = str(candidate.get("id", ""))
        cand_fp = compute_job_fingerprint(candidate)

        match_idx = id_index.get(cand_id)
        if match_idx is None:
            match_idx = fp_index.get(cand_fp)

        if match_idx is None:
            new_idx = len(merged)
            merged.append(candidate)
            if cand_id:
                id_index[cand_id] = new_idx
            fp_index[cand_fp] = new_idx
            inserted += 1
        else:
            current = merged[match_idx]
            # Preserve existing primary id and initial scraped_at
            preserved_id = current.get("id", cand_id)
            preserved_scraped_at = current.get("scraped_at") or candidate.get("scraped_at")

            comparable_current = dict(current)
            comparable_candidate = dict(candidate)
            comparable_candidate["id"] = preserved_id
            comparable_candidate["scraped_at"] = preserved_scraped_at

            # Check if substantive fields changed (ignoring last_seen_at)
            cur_cmp = {k: v for k, v in comparable_current.items() if k != "last_seen_at"}
            cand_cmp = {k: v for k, v in comparable_candidate.items() if k != "last_seen_at"}

            if cur_cmp == cand_cmp:
                current["last_seen_at"] = candidate.get("last_seen_at", current.get("last_seen_at"))
                unchanged += 1
            else:
                merged[match_idx] = comparable_candidate
                id_index[str(preserved_id)] = match_idx
                fp_index[compute_job_fingerprint(comparable_candidate)] = match_idx
                updated += 1

    return merged, inserted, updated, unchanged


def archive_expired_jobs(
    root_dir: Path = ROOT_DIR,
    today: date | None = None,
    remove_from_active: bool = True,
    dry_run: bool = False,
) -> ArchiveSummary:
    """Mark outdated jobs as expired and archive expired/closed jobs to data/archive/YYYY-MM.json."""
    root_dir = Path(root_dir)
    jobs_path = root_dir / "data" / "jobs.json"
    archive_dir = root_dir / "data" / "archive"

    dataset = _load_dataset(jobs_path)
    items: list[dict[str, Any]] = [copy.deepcopy(it) for it in dataset.get("items", [])]

    expired_marked = expire_outdated_jobs(items, today=today)

    to_archive_by_month: dict[str, list[dict[str, Any]]] = {}
    active_items: list[dict[str, Any]] = []

    for item in items:
        if item.get("status") in {"expired", "closed"}:
            ym = _determine_archive_month(item)
            to_archive_by_month.setdefault(ym, []).append(item)
            if not remove_from_active:
                active_items.append(item)
        else:
            active_items.append(item)

    archived_count = sum(len(v) for v in to_archive_by_month.values())
    written_archive_files: list[str] = []

    for ym, month_items in sorted(to_archive_by_month.items()):
        archive_path = archive_dir / f"{ym}.json"
        rel_archive = f"data/archive/{ym}.json"
        written_archive_files.append(rel_archive)
        if not dry_run:
            existing_archive = _load_dataset(archive_path)
            merged_archive, _, _, _ = _merge_item_lists(existing_archive.get("items", []), month_items)
            _save_dataset(archive_path, merged_archive)

    if not dry_run and (expired_marked > 0 or (remove_from_active and archived_count > 0)):
        _save_dataset(jobs_path, active_items)

    return ArchiveSummary(
        expired_marked=expired_marked,
        archived_count=archived_count,
        remaining_active=len(active_items),
        archive_files=written_archive_files,
    )


def merge_jobs_into_dataset(
    incoming_jobs: list[dict[str, Any]],
    root_dir: Path = ROOT_DIR,
    today: date | None = None,
    archive_expired: bool = False,
    remove_archived_from_active: bool = True,
    dry_run: bool = False,
) -> MergeSummary:
    """Validate and merge incoming_jobs into data/jobs.json, expire outdated jobs, and optionally archive."""
    root_dir = Path(root_dir)
    jobs_path = root_dir / "data" / "jobs.json"
    known_company_ids = _load_known_company_ids(root_dir)

    # Validate incoming batch first
    incoming_errors = _validate_jobs_batch(incoming_jobs, known_company_ids)
    if incoming_errors:
        raise ValueError(f"Incoming jobs failed schema validation: {incoming_errors}")

    dataset = _load_dataset(jobs_path)
    existing_items: list[dict[str, Any]] = dataset.get("items", [])

    merged_items, inserted, updated, unchanged = _merge_item_lists(existing_items, incoming_jobs)
    expired_marked = expire_outdated_jobs(merged_items, today=today)

    # Validate full merged list before persisting
    merged_errors = _validate_jobs_batch(merged_items, known_company_ids)
    if merged_errors:
        raise ValueError(f"Merged jobs dataset failed validation: {merged_errors}")

    archived_count = 0
    archive_files: list[str] = []

    if archive_expired:
        archive_dir = root_dir / "data" / "archive"
        to_archive_by_month: dict[str, list[dict[str, Any]]] = {}
        kept_items: list[dict[str, Any]] = []

        for item in merged_items:
            if item.get("status") in {"expired", "closed"}:
                ym = _determine_archive_month(item)
                to_archive_by_month.setdefault(ym, []).append(item)
                if not remove_archived_from_active:
                    kept_items.append(item)
            else:
                kept_items.append(item)

        archived_count = sum(len(v) for v in to_archive_by_month.values())
        for ym, month_items in sorted(to_archive_by_month.items()):
            archive_path = archive_dir / f"{ym}.json"
            archive_files.append(f"data/archive/{ym}.json")
            if not dry_run:
                existing_archive = _load_dataset(archive_path)
                merged_archive, _, _, _ = _merge_item_lists(existing_archive.get("items", []), month_items)
                _save_dataset(archive_path, merged_archive)

        merged_items = kept_items

    if not dry_run:
        _save_dataset(jobs_path, merged_items)

    return MergeSummary(
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        expired_marked=expired_marked,
        archived=archived_count,
        active_total=len(merged_items),
        archive_files=archive_files,
    )
