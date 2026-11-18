"""URL + SHA-256 content-hash deduplication and raw_inbox persistence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from scrapers.core.extractors import AttachmentLink

TZ_CN = timezone(timedelta(hours=8))


def now_iso_cn() -> str:
    """Return current timestamp in ISO-8601 format (+08:00)."""
    return datetime.now(TZ_CN).replace(microsecond=0).isoformat()


def _normalize_for_hash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def compute_content_hash(url: str, body_text: str, attachments_text: str = "") -> str:
    """Compute deterministic SHA-256 hash over canonical URL, body text, and extracted attachments."""
    canonical_url = (url or "").strip()
    norm_body = _normalize_for_hash(body_text)
    norm_att = _normalize_for_hash(attachments_text)
    payload = f"url:{canonical_url}\nbody:{norm_body}\nattachments:{norm_att}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def short_hash(content_hash: str, length: int = 12) -> str:
    """Return shortened prefix of a content SHA-256 hash (default 12 hex chars)."""
    return (content_hash or "")[: max(8, length)]


@dataclass(slots=True)
class IngestResult:
    """Outcome of evaluating and persisting a scraped announcement."""

    url: str
    source_id: str
    status: str  # "new" | "changed" | "unchanged"
    content_hash: str
    written: bool
    inbox_path: Path | None = None


@dataclass
class CrawlStateStore:
    """Manage crawl_state.json deduplication index and raw_inbox/*.txt output queue."""

    root_dir: Path
    state_path: Path | None = None
    inbox_dir: Path | None = None
    _state: dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.root_dir = Path(self.root_dir)
        if self.state_path is None:
            self.state_path = self.root_dir / "data" / "raw_cache" / "crawl_state.json"
        else:
            self.state_path = Path(self.state_path)
        if self.inbox_dir is None:
            self.inbox_dir = self.root_dir / "raw_inbox"
        else:
            self.inbox_dir = Path(self.inbox_dir)
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        assert self.state_path is not None
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("records"), dict):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "schema_version": "1.0",
            "updated_at": now_iso_cn(),
            "records": {},
        }

    @property
    def records(self) -> dict[str, dict[str, Any]]:
        return self._state.setdefault("records", {})

    def save_state(self) -> None:
        """Persist crawl_state.json to disk."""
        assert self.state_path is not None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state["updated_at"] = now_iso_cn()
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def check_status(self, url: str, content_hash: str) -> str:
        """Return 'new', 'changed', or 'unchanged' for a given URL and content_hash."""
        canonical_url = url.strip()
        existing = self.records.get(canonical_url)
        if existing is None:
            return "new"
        if existing.get("content_hash") != content_hash:
            return "changed"
        return "unchanged"

    def is_new_or_changed(self, url: str, content_hash: str) -> bool:
        """Return True only when the announcement is brand new or its content hash changed."""
        return self.check_status(url, content_hash) in {"new", "changed"}

    @staticmethod
    def format_inbox_content(
        *,
        source_id: str,
        source_name: str,
        source_url: str,
        title: str,
        published_at: str | None,
        scraped_at: str,
        content_hash: str,
        body_text: str,
        attachments: list[AttachmentLink | dict[str, str]] | None = None,
        attachments_text_map: dict[str, str] | None = None,
    ) -> str:
        """Build the plain-text payload written to raw_inbox/<source_id>_<short_hash>.txt."""
        att_list = attachments or []
        att_lines: list[str] = []
        for idx, att in enumerate(att_list, start=1):
            if isinstance(att, AttachmentLink):
                a_name, a_url, a_type = att.name, att.url, att.file_type
            else:
                a_name = str(att.get("name", ""))
                a_url = str(att.get("url", ""))
                a_type = str(att.get("file_type", "other"))
            att_lines.append(f"  - [{idx}] {a_name} | {a_url} | type={a_type}")

        header_lines = [
            "=== ANNOUNCEMENT METADATA ===",
            f"source_id: {source_id}",
            f"source_name: {source_name}",
            f"source_url: {source_url}",
            f"title: {title}",
            f"published_at: {published_at or 'null'}",
            f"scraped_at: {scraped_at}",
            f"content_hash: {content_hash}",
            "attachments:" if att_lines else "attachments: []",
        ]
        if att_lines:
            header_lines.extend(att_lines)
        header_lines.append("=============================")

        sections = ["\n".join(header_lines), "=== MAIN BODY TEXT ===", (body_text or "").strip()]

        if attachments_text_map:
            att_section_lines = ["=== EXTRACTED ATTACHMENTS ==="]
            for att_label, extracted_txt in attachments_text_map.items():
                att_section_lines.append(f"--- [{att_label}] ---")
                att_section_lines.append((extracted_txt or "").strip())
            sections.append("\n".join(att_section_lines))

        return "\n\n".join(sections).strip() + "\n"

    def ingest_announcement(
        self,
        *,
        source_id: str,
        source_name: str,
        source_url: str,
        title: str,
        published_at: str | None,
        body_text: str,
        attachments: list[AttachmentLink | dict[str, str]] | None = None,
        attachments_text_map: dict[str, str] | None = None,
        scraped_at: str | None = None,
        dry_run: bool = False,
    ) -> IngestResult:
        """Deduplicate announcement by URL + SHA-256 hash and write to raw_inbox if new/changed."""
        assert self.inbox_dir is not None
        canonical_url = source_url.strip()
        ts = scraped_at or now_iso_cn()

        combined_attachments_text = ""
        if attachments_text_map:
            combined_attachments_text = "\n\n".join(
                f"{k}\n{v}" for k, v in sorted(attachments_text_map.items())
            )

        c_hash = compute_content_hash(canonical_url, body_text, combined_attachments_text)
        status = self.check_status(canonical_url, c_hash)
        s_hash = short_hash(c_hash, 12)
        inbox_file_path = self.inbox_dir / f"{source_id}_{s_hash}.txt"

        if status == "unchanged":
            if not dry_run and canonical_url in self.records:
                self.records[canonical_url]["last_seen_at"] = ts
                self.save_state()
            return IngestResult(
                url=canonical_url,
                source_id=source_id,
                status="unchanged",
                content_hash=c_hash,
                written=False,
                inbox_path=None,
            )

        if dry_run:
            return IngestResult(
                url=canonical_url,
                source_id=source_id,
                status=status,
                content_hash=c_hash,
                written=False,
                inbox_path=inbox_file_path,
            )

        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        txt_payload = self.format_inbox_content(
            source_id=source_id,
            source_name=source_name,
            source_url=canonical_url,
            title=title,
            published_at=published_at,
            scraped_at=ts,
            content_hash=c_hash,
            body_text=body_text,
            attachments=attachments,
            attachments_text_map=attachments_text_map,
        )
        inbox_file_path.write_text(txt_payload, encoding="utf-8")

        prev_record = self.records.get(canonical_url, {})
        first_seen = prev_record.get("first_seen_at", ts)
        self.records[canonical_url] = {
            "source_id": source_id,
            "url": canonical_url,
            "title": title,
            "published_at": published_at,
            "content_hash": c_hash,
            "first_seen_at": first_seen,
            "last_seen_at": ts,
            "inbox_file": f"raw_inbox/{inbox_file_path.name}",
        }
        self.save_state()

        return IngestResult(
            url=canonical_url,
            source_id=source_id,
            status=status,
            content_hash=c_hash,
            written=True,
            inbox_path=inbox_file_path,
        )
