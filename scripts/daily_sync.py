"""Daily one-click sync pipeline for Gongyi-Jobs.

Workflow:
1. Run incremental scrapers for all enabled sources in config/sources.json.
2. Mark outdated active jobs (deadline_at < today) as expired and archive by month.
3. Run workspace data & schema validation (same checks as scripts/validate_data.py).
4. Print newly ingested job counts and the pending raw_inbox/ file checklist.

Usage examples:
  python scripts/daily_sync.py
  python scripts/daily_sync.py --dry-run
  python scripts/daily_sync.py --category public_institution --max-items 10
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scrapers.base import CrawlRunSummary
from scrapers.core.merger import ArchiveSummary, archive_expired_jobs
from scrapers.run import load_sources_config, run_scrapers
from scripts.validate_data import validate_workspace


@dataclass(slots=True)
class InboxItemInfo:
    """Lightweight metadata summary for a pending raw_inbox text file."""

    rel_path: str
    size_bytes: int
    source_id: str = ""
    title: str = ""
    published_at: str = ""


@dataclass(slots=True)
class DailySyncReport:
    """Structured execution report returned by run_daily_sync."""

    dry_run: bool
    scrape_summaries: list[CrawlRunSummary]
    archive_summary: ArchiveSummary | None
    validation_errors: list[str]
    jobs_before: int
    jobs_after_scrape: int
    jobs_after_archive: int
    newly_inserted_jobs: int
    updated_jobs: int
    new_inbox_files: list[str] = field(default_factory=list)
    pending_inbox_files: list[InboxItemInfo] = field(default_factory=list)
    companies_count: int = 0
    sources_count: int = 0


def _load_job_items(root_dir: Path) -> list[dict[str, Any]]:
    jobs_path = root_dir / "data" / "jobs.json"
    if not jobs_path.exists():
        return []
    data = json.loads(jobs_path.read_text(encoding="utf-8"))
    items = data.get("items")
    return list(items) if isinstance(items, list) else []


def _count_dataset_totals(root_dir: Path) -> tuple[int, int, int]:
    jobs_count = len(_load_job_items(root_dir))
    companies_path = root_dir / "data" / "companies.json"
    companies_count = 0
    if companies_path.exists():
        cdata = json.loads(companies_path.read_text(encoding="utf-8"))
        if isinstance(cdata.get("items"), list):
            companies_count = len(cdata["items"])
    sources_count = len(load_sources_config(root_dir))
    return jobs_count, companies_count, sources_count


def list_pending_inbox_files(root_dir: Path = ROOT_DIR) -> list[InboxItemInfo]:
    """List pending *.txt announcement files in raw_inbox/ sorted by filename."""
    inbox_dir = root_dir / "raw_inbox"
    if not inbox_dir.exists():
        return []

    results: list[InboxItemInfo] = []
    for path in sorted(inbox_dir.glob("*.txt")):
        meta: dict[str, str] = {}
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for _ in range(16):
                    line = fh.readline()
                    if not line:
                        break
                    stripped = line.strip()
                    if stripped.startswith("==="):
                        continue
                    if ":" in stripped:
                        k, _, v = stripped.partition(":")
                        meta[k.strip()] = v.strip()
        except OSError:
            pass

        results.append(
            InboxItemInfo(
                rel_path=f"raw_inbox/{path.name}",
                size_bytes=path.stat().st_size,
                source_id=meta.get("source_id", ""),
                title=meta.get("title", ""),
                published_at=meta.get("published_at", ""),
            )
        )
    return results


def run_daily_sync(
    *,
    root_dir: Path = ROOT_DIR,
    source_id: str | None = None,
    category: str | None = None,
    dry_run: bool = False,
    include_disabled: bool = False,
    max_items: int = 12,
    min_interval_sec: float = 1.0,
    skip_archive: bool = False,
    today: date | None = None,
) -> DailySyncReport:
    """Execute the end-to-end daily sync pipeline and return a DailySyncReport."""
    root_dir = Path(root_dir)
    sources_by_id = {
        str(src.get("source_id")): src
        for src in load_sources_config(root_dir)
        if src.get("source_id")
    }

    before_items = _load_job_items(root_dir)
    before_ids = {str(it.get("id")) for it in before_items if it.get("id")}

    summaries = run_scrapers(
        root_dir=root_dir,
        source_id=source_id,
        category=category,
        dry_run=dry_run,
        include_disabled=include_disabled,
        max_items=max_items,
        min_interval_sec=min_interval_sec,
    )

    after_scrape_items = _load_job_items(root_dir)
    after_scrape_ids = {str(it.get("id")) for it in after_scrape_items if it.get("id")}

    if dry_run:
        newly_inserted = sum(
            s.new_count
            for s in summaries
            if sources_by_id.get(s.source_id, {}).get("category") == "enterprise"
        )
        updated_jobs = sum(
            s.changed_count
            for s in summaries
            if sources_by_id.get(s.source_id, {}).get("category") == "enterprise"
        )
    else:
        newly_inserted = len(after_scrape_ids - before_ids)
        updated_jobs = sum(
            s.changed_count
            for s in summaries
            if sources_by_id.get(s.source_id, {}).get("category") == "enterprise"
        )

    archive_summary: ArchiveSummary | None = None
    if not skip_archive:
        archive_summary = archive_expired_jobs(
            root_dir=root_dir,
            today=today,
            remove_from_active=True,
            dry_run=dry_run,
        )

    validation_errors = validate_workspace(root_dir)
    jobs_after_archive, companies_count, sources_count = _count_dataset_totals(root_dir)

    new_inbox_files: list[str] = []
    for s in summaries:
        for f in s.inbox_files:
            if f not in new_inbox_files:
                new_inbox_files.append(f)

    pending_inbox_files = list_pending_inbox_files(root_dir)

    return DailySyncReport(
        dry_run=dry_run,
        scrape_summaries=summaries,
        archive_summary=archive_summary,
        validation_errors=validation_errors,
        jobs_before=len(before_items),
        jobs_after_scrape=len(after_scrape_items),
        jobs_after_archive=jobs_after_archive,
        newly_inserted_jobs=newly_inserted,
        updated_jobs=updated_jobs,
        new_inbox_files=new_inbox_files,
        pending_inbox_files=pending_inbox_files,
        companies_count=companies_count,
        sources_count=sources_count,
    )


def format_sync_report(report: DailySyncReport) -> str:
    """Format a human-readable summary of the daily sync run."""
    mode = "DRY-RUN" if report.dry_run else "LIVE"
    lines: list[str] = [f"=== Daily Sync ({mode}) ==="]

    lines.append(f"[1/4] 增量抓取 enabled 源 ({len(report.scrape_summaries)} 个):")
    if not report.scrape_summaries:
        lines.append("  (未匹配到启用的源)")
    total_errors = 0
    for s in report.scrape_summaries:
        lines.append(
            f"  - {s.source_id} ({s.source_name}): lists={s.list_urls_scanned}, "
            f"fetched={s.announcements_fetched}, new={s.new_count}, "
            f"changed={s.changed_count}, unchanged={s.unchanged_count}, errors={len(s.errors)}"
        )
        for err in s.errors:
            total_errors += 1
            lines.append(f"      ! {err}")

    lines.append("[2/4] 过期检测与按月归档:")
    if report.archive_summary is None:
        lines.append("  - 已跳过归档 (--no-archive)")
    else:
        arch = report.archive_summary
        arch_files = ", ".join(arch.archive_files) if arch.archive_files else "无"
        lines.append(
            f"  - 新标记过期={arch.expired_marked}, 本次归档={arch.archived_count}, "
            f"活跃保留={arch.remaining_active}, 归档文件={arch_files}"
        )

    lines.append("[3/4] 数据与 Schema 校验 (validate_data):")
    if report.validation_errors:
        lines.append(f"  - [FAILED] {len(report.validation_errors)} error(s):")
        for err in report.validation_errors:
            lines.append(f"      * {err}")
    else:
        lines.append(
            f"  - [OK] Validation passed (jobs={report.jobs_after_archive}, "
            f"companies={report.companies_count}, sources={report.sources_count})."
        )

    lines.append("[4/4] 同步结果汇总:")
    lines.append(
        f"  - 本次新增入库岗位数: {report.newly_inserted_jobs} "
        f"(更新: {report.updated_jobs}, 入库前: {report.jobs_before}, "
        f"当前有效在招: {report.jobs_after_archive}, 抓取报错: {total_errors})"
    )

    if report.new_inbox_files:
        lines.append(f"  - 本次新写入 raw_inbox/ 公告 ({len(report.new_inbox_files)} 份):")
        for path in report.new_inbox_files:
            lines.append(f"      + {path}")

    lines.append(f"  - raw_inbox/ 待人工或AI解析文件清单 ({len(report.pending_inbox_files)} 份):")
    if not report.pending_inbox_files:
        lines.append("      (暂无待解析文件)")
    else:
        for item in report.pending_inbox_files:
            meta_parts = [p for p in (item.source_id, item.published_at, item.title) if p]
            desc = " | ".join(meta_parts) if meta_parts else "无元信息"
            lines.append(f"      * {item.rel_path} ({item.size_bytes} B) — {desc}")

    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gongyi-Jobs Daily Sync Pipeline")
    parser.add_argument("--source", dest="source_id", default=None, help="Run only one specific source_id.")
    parser.add_argument(
        "--category",
        choices=["public_institution", "enterprise"],
        default=None,
        help="Filter enabled sources by category.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run without writing raw_inbox/ or data/*.json.")
    parser.add_argument("--include-disabled", action="store_true", help="Also run disabled sources.")
    parser.add_argument("--max-items", type=int, default=12, help="Max items to fetch per source (default: 12).")
    parser.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        help="Minimum HTTP interval per host in seconds (default: 1.0).",
    )
    parser.add_argument("--no-archive", action="store_true", help="Skip marking expired jobs and archiving.")
    parser.add_argument("--strict", action="store_true", help="Exit with code 1 if any scraper errors occurred.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        report = run_daily_sync(
            root_dir=ROOT_DIR,
            source_id=args.source_id,
            category=args.category,
            dry_run=args.dry_run,
            include_disabled=args.include_disabled,
            max_items=args.max_items,
            min_interval_sec=args.min_interval,
            skip_archive=args.no_archive,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(format_sync_report(report))

    if report.validation_errors:
        return 1
    if args.strict and any(s.errors for s in report.scrape_summaries):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
