"""Unified scraper adapter base class and registry."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scrapers.core.extractors import (
    LEGACY_UNSUPPORTED_TYPES,
    extract_attachment,
    extract_html,
)
from scrapers.core.http_client import PoliteHttpClient
from scrapers.core.storage import CrawlStateStore, IngestResult

RECRUIT_KEYWORDS = (
    "招聘",
    "招考",
    "选聘",
    "选调",
    "引进",
    "人才",
    "事业单位",
    "岗位",
    "公告",
    "简章",
    "笔试",
    "面试",
)

_INLINE_DATE_RE = re.compile(r"(20\d{2})[-年/.](0?[1-9]|1[0-2])[-月/.](0?[1-9]|[12]\d|3[01])")


@dataclass(slots=True)
class AnnouncementStub:
    """Lightweight announcement link entry discovered on a list page."""

    url: str
    title: str
    published_at: str | None = None


@dataclass(slots=True)
class CrawlRunSummary:
    """Execution summary for a single source adapter run."""

    source_id: str
    source_name: str
    dry_run: bool
    list_urls_scanned: int = 0
    announcements_fetched: int = 0
    new_count: int = 0
    changed_count: int = 0
    unchanged_count: int = 0
    inbox_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BaseScraperAdapter:
    """Base adapter for government recruitment announcement sites and career portals."""

    adapter_name: str = "generic_gov"
    filter_by_keywords: bool = True

    def __init__(
        self,
        source_config: dict[str, Any],
        http_client: PoliteHttpClient,
        state_store: CrawlStateStore,
    ) -> None:
        self.source_config = source_config
        self.source_id: str = str(source_config["source_id"])
        self.source_name: str = str(source_config["name"])
        self.base_url: str = str(source_config["base_url"])
        self.list_urls: list[str] = list(source_config.get("list_urls") or [])
        self.http_client = http_client
        self.state_store = state_store

    def parse_list_page(self, html: str, page_url: str) -> list[AnnouncementStub]:
        """Extract announcement stubs from a list page HTML."""
        soup = BeautifulSoup(html or "", "html.parser")
        stubs: list[AnnouncementStub] = []
        seen_urls: set[str] = set()
        base_host = urlparse(self.base_url).netloc.lower()

        for a_tag in soup.find_all("a", href=True):
            raw_href = str(a_tag["href"]).strip()
            if not raw_href or raw_href.startswith(("javascript:", "mailto:", "#")):
                continue
            full_url = urljoin(page_url, raw_href)
            parsed = urlparse(full_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if base_host and parsed.netloc.lower() != base_host:
                continue

            title = re.sub(r"\s+", " ", str(a_tag.get("title") or a_tag.get_text(" ", strip=True))).strip()
            if len(title) < 6:
                continue
            if self.filter_by_keywords and not any(kw in title for kw in RECRUIT_KEYWORDS):
                continue

            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            parent_text = a_tag.parent.get_text(" ", strip=True) if a_tag.parent else ""
            pub_date: str | None = None
            m = _INLINE_DATE_RE.search(parent_text)
            if m:
                pub_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

            stubs.append(AnnouncementStub(url=full_url, title=title, published_at=pub_date))

        return stubs

    def process_detail_page(
        self,
        stub: AnnouncementStub,
        *,
        fetch_attachments: bool = True,
        dry_run: bool = False,
    ) -> IngestResult:
        """Fetch an announcement detail page, extract body and attachments, and deduplicate."""
        resp = self.http_client.get(stub.url)
        html_res = extract_html(resp.text, base_url=resp.url)

        title = html_res.title or stub.title
        published_at = html_res.published_at or stub.published_at

        attachments_text_map: dict[str, str] = {}
        for att in html_res.attachments:
            label = f"{att.name} ({att.url})"
            if att.file_type in LEGACY_UNSUPPORTED_TYPES:
                degraded = extract_attachment(b"", att.url)
                attachments_text_map[label] = degraded.text
            elif fetch_attachments:
                try:
                    att_bytes = self.http_client.fetch_bytes(att.url)
                    extracted = extract_attachment(att_bytes, att.url)
                    attachments_text_map[label] = extracted.text
                except Exception as exc:
                    attachments_text_map[label] = f"[附件下载失败] {att.name} ({att.url}): {exc}"

        return self.state_store.ingest_announcement(
            source_id=self.source_id,
            source_name=self.source_name,
            source_url=resp.url,
            title=title,
            published_at=published_at,
            body_text=html_res.text,
            attachments=html_res.attachments,
            attachments_text_map=attachments_text_map,
            dry_run=dry_run,
        )

    def run(
        self,
        *,
        dry_run: bool = False,
        max_items: int = 10,
        fetch_attachments: bool = True,
        probe_base_when_empty: bool = True,
    ) -> CrawlRunSummary:
        """Run incremental crawl across configured list_urls."""
        summary = CrawlRunSummary(
            source_id=self.source_id,
            source_name=self.source_name,
            dry_run=dry_run,
        )

        target_list_urls = list(self.list_urls)
        if not target_list_urls and probe_base_when_empty and dry_run:
            target_list_urls = [self.base_url]

        discovered_stubs: list[AnnouncementStub] = []
        seen_urls: set[str] = set()

        for list_url in target_list_urls:
            try:
                resp = self.http_client.get(list_url)
                summary.list_urls_scanned += 1
                for stub in self.parse_list_page(resp.text, resp.url):
                    if stub.url not in seen_urls:
                        seen_urls.add(stub.url)
                        discovered_stubs.append(stub)
            except Exception as exc:
                summary.errors.append(f"List fetch failed ({list_url}): {exc}")

        for stub in discovered_stubs[: max(0, max_items)]:
            try:
                res = self.process_detail_page(
                    stub,
                    fetch_attachments=fetch_attachments,
                    dry_run=dry_run,
                )
                summary.announcements_fetched += 1
                if res.status == "new":
                    summary.new_count += 1
                elif res.status == "changed":
                    summary.changed_count += 1
                else:
                    summary.unchanged_count += 1

                if res.inbox_path is not None and (res.written or dry_run):
                    summary.inbox_files.append(f"raw_inbox/{res.inbox_path.name}")
            except Exception as exc:
                summary.errors.append(f"Detail fetch failed ({stub.url}): {exc}")

        return summary


class HenanHrssAdapter(BaseScraperAdapter):
    """Adapter for Henan Provincial Department of Human Resources and Social Security."""

    adapter_name = "henan_hrss"


class HenanRsksAdapter(BaseScraperAdapter):
    """Adapter for Henan Personnel Examination Center (hnrsks.com)."""

    adapter_name = "henan_rsks"


ADAPTER_REGISTRY: dict[str, type[BaseScraperAdapter]] = {
    "generic_gov": BaseScraperAdapter,
    "henan_hrss": HenanHrssAdapter,
    "henan_rsks": HenanRsksAdapter,
}


def get_adapter_class(adapter_name: str) -> type[BaseScraperAdapter]:
    """Return adapter class by name, defaulting to BaseScraperAdapter."""
    return ADAPTER_REGISTRY.get(adapter_name, BaseScraperAdapter)
