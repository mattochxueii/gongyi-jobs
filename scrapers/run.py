"""CLI entry point for running incremental scrapers and lifecycle maintenance.

Usage examples:
  python -m scrapers.run --dry-run
  python -m scrapers.run --source henan_hrss_gov --dry-run --include-disabled
  python scrapers/run.py --expire-and-archive --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure workspace root is importable when invoked as `python scrapers/run.py`
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scrapers.base import CrawlRunSummary, get_adapter_class
from scrapers.core.http_client import PoliteHttpClient
from scrapers.core.merger import archive_expired_jobs
from scrapers.core.storage import CrawlStateStore


def load_sources_config(root_dir: Path = ROOT_DIR) -> list[dict[str, Any]]:
    """Load sources list from config/sources.json."""
    config_path = root_dir / "config" / "sources.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return list(data.get("sources") or [])


def select_sources(
    sources: list[dict[str, Any]],
    source_id: str | None = None,
    include_disabled: bool = False,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Filter configured sources by source_id, category, and enabled state."""
    selected: list[dict[str, Any]] = []
    for src in sources:
        if source_id and src.get("source_id") != source_id:
            continue
        if category and src.get("category") != category:
            continue
        if not include_disabled and not source_id and not src.get("enabled", False):
            continue
        selected.append(src)
    return selected


def run_scrapers(
    *,
    root_dir: Path = ROOT_DIR,
    source_id: str | None = None,
    category: str | None = None,
    dry_run: bool = False,
    include_disabled: bool = False,
    max_items: int = 5,
    min_interval_sec: float = 1.0,
) -> list[CrawlRunSummary]:
    """Run selected source adapters and return their CrawlRunSummary objects."""
    all_sources = load_sources_config(root_dir)
    target_sources = select_sources(
        all_sources,
        source_id=source_id,
        include_disabled=include_disabled,
        category=category,
    )

    if source_id and not target_sources:
        raise ValueError(f"Unknown source_id: {source_id!r}")

    state_store = CrawlStateStore(root_dir=root_dir)
    summaries: list[CrawlRunSummary] = []

    with PoliteHttpClient(min_interval_sec=min_interval_sec) as client:
        for src_cfg in target_sources:
            adapter_cls = get_adapter_class(str(src_cfg.get("adapter", "generic_gov")))
            adapter = adapter_cls(src_cfg, http_client=client, state_store=state_store)
            summary = adapter.run(dry_run=dry_run, max_items=max_items)
            summaries.append(summary)

    return summaries


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gongyi-Jobs Incremental Scraper & Lifecycle CLI")
    parser.add_argument(
        "--source",
        dest="source_id",
        default=None,
        help="Run a specific source_id from config/sources.json (runs all enabled sources if omitted).",
    )
    parser.add_argument(
        "--category",
        choices=["public_institution", "enterprise"],
        default=None,
        help="Filter configured sources by category.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode: fetch and evaluate without writing to raw_inbox/ or data/*.json.",
    )
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include sources where enabled=false when running.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=5,
        help="Maximum detail pages to fetch per source (default: 5).",
    )
    parser.add_argument(
        "--expire-and-archive",
        action="store_true",
        help="Also mark deadline_at < today jobs as expired and archive them to data/archive/YYYY-MM.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        summaries = run_scrapers(
            root_dir=ROOT_DIR,
            source_id=args.source_id,
            category=args.category,
            dry_run=args.dry_run,
            include_disabled=args.include_disabled,
            max_items=args.max_items,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    mode_tag = "DRY-RUN" if args.dry_run else "LIVE"
    if not summaries:
        print(f"[{mode_tag}] No active sources matched (use --source <id> or --include-disabled).")
    else:
        for s in summaries:
            print(
                f"[{mode_tag}] source={s.source_id} ({s.source_name}) | "
                f"lists={s.list_urls_scanned} fetched={s.announcements_fetched} "
                f"new={s.new_count} changed={s.changed_count} unchanged={s.unchanged_count} "
                f"errors={len(s.errors)}"
            )
            for err in s.errors:
                print(f"  ! {err}", file=sys.stderr)

    if args.expire_and_archive:
        arch = archive_expired_jobs(root_dir=ROOT_DIR, dry_run=args.dry_run)
        print(
            f"[{mode_tag}] Lifecycle archive: expired_marked={arch.expired_marked}, "
            f"archived={arch.archived_count}, remaining_active={arch.remaining_active}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
