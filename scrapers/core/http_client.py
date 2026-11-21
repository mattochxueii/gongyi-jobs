"""HTTP client with polite rate-limiting, retry/backoff, and Chinese gov-site encoding detection."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 GongyiJobsBot/1.0"
)

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_CHARSET_HEADER_RE = re.compile(r"charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)", re.IGNORECASE)
_META_CHARSET_RE = re.compile(
    rb"<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)",
    re.IGNORECASE,
)
_XML_ENCODING_RE = re.compile(
    rb"<\?xml[^>]+encoding\s*=\s*[\"']([a-zA-Z0-9._-]+)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class HttpResponse:
    """Normalized HTTP response wrapper."""

    url: str
    status_code: int
    content: bytes
    headers: dict[str, str]
    encoding: str
    text: str


def _normalize_charset_name(raw_name: str | None) -> str | None:
    if not raw_name:
        return None
    cleaned = raw_name.strip().lower().replace("_", "-")
    if cleaned in {"utf-8", "utf8", "utf-8-sig"}:
        return "utf-8"
    if cleaned in {"gbk", "gb2312", "gb-2312", "gb2312-80", "cp936", "ms936"}:
        return "gbk"
    if cleaned in {"gb18030", "gb-18030"}:
        return "gb18030"
    if cleaned in {"utf-16", "utf16", "utf-16le", "utf-16be"}:
        return "utf-16"
    # Generic Western default headers on misconfigured domestic gov servers are ignored
    if cleaned in {"iso-8859-1", "latin1", "latin-1", "us-ascii", "ascii"}:
        return None
    return cleaned


def _extract_meta_charset(raw_bytes: bytes) -> str | None:
    head = raw_bytes[:4096]
    for pattern in (_META_CHARSET_RE, _XML_ENCODING_RE):
        match = pattern.search(head)
        if match:
            try:
                raw_charset = match.group(1).decode("ascii", errors="ignore")
                normalized = _normalize_charset_name(raw_charset)
                if normalized:
                    return normalized
            except Exception:
                continue
    return None


def _extract_header_charset(content_type: str | None) -> str | None:
    if not content_type:
        return None
    match = _CHARSET_HEADER_RE.search(content_type)
    if not match:
        return None
    return _normalize_charset_name(match.group(1))


def detect_encoding(raw_bytes: bytes, content_type: str | None = None) -> str:
    """Detect encoding of raw HTML/text bytes (UTF-8 / GBK / GB18030)."""
    encoding, _ = decode_html_bytes(raw_bytes, content_type=content_type)
    return encoding


def decode_html_bytes(raw_bytes: bytes, content_type: str | None = None) -> tuple[str, str]:
    """Decode raw HTML/text bytes and return (canonical_encoding, decoded_text).

    Handles UTF-8 (with or without BOM), GBK/GB2312, and GB18030 (including 4-byte
    sequences common in Chinese government announcements and rare CJK characters).
    """
    if not raw_bytes:
        return "utf-8", ""

    # 1. Explicit BOM check
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return "utf-8", raw_bytes.decode("utf-8-sig", errors="replace")
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", raw_bytes.decode("utf-16", errors="replace")

    meta_charset = _extract_meta_charset(raw_bytes)
    header_charset = _extract_header_charset(content_type)

    candidates: list[str] = []
    for hint in (meta_charset, header_charset):
        if hint and hint not in candidates:
            candidates.append(hint)
            if hint == "gbk" and "gb18030" not in candidates:
                candidates.append("gb18030")

    for fallback in ("utf-8", "gbk", "gb18030"):
        if fallback not in candidates:
            candidates.append(fallback)

    # If the hint said gbk/gb18030, check whether the bytes are actually strict UTF-8
    # (unless they only contain ASCII). Wait: only override if strict gbk fails or if
    # both succeed and UTF-8 has clear multi-byte UTF-8 sequences.
    for enc in candidates:
        try:
            text = raw_bytes.decode(enc, errors="strict")
            return enc, text
        except (UnicodeDecodeError, LookupError):
            continue

    # Final fallback: GB18030 with replacement
    return "gb18030", raw_bytes.decode("gb18030", errors="replace")


class PoliteHttpClient:
    """HTTP client wrapper with per-host rate limiting, retries, and encoding detection."""

    def __init__(
        self,
        min_interval_sec: float = 1.0,
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval_sec = max(0.0, float(min_interval_sec))
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff_factor = max(0.0, float(backoff_factor))
        self._sleep = sleep_fn
        self._clock = clock_fn
        self._last_request_by_host: dict[str, float] = {}

        merged_headers = dict(DEFAULT_HEADERS)
        if headers:
            merged_headers.update(headers)

        self._merged_headers = merged_headers
        self._has_custom_transport = transport is not None
        self._ipv4_only_hosts: set[str] = set()
        self._ipv4_client: httpx.Client | None = None

        self._client = httpx.Client(
            headers=merged_headers,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            transport=transport,
        )

    def _get_ipv4_client(self) -> httpx.Client:
        if self._ipv4_client is None:
            self._ipv4_client = httpx.Client(
                headers=self._merged_headers,
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                transport=httpx.HTTPTransport(local_address="0.0.0.0"),
            )
        return self._ipv4_client

    def close(self) -> None:
        self._client.close()
        if self._ipv4_client is not None:
            self._ipv4_client.close()

    def __enter__(self) -> PoliteHttpClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _respect_rate_limit(self, url: str) -> None:
        if self.min_interval_sec <= 0:
            return
        host = urlparse(url).netloc.lower()
        if not host:
            return
        now = self._clock()
        last_ts = self._last_request_by_host.get(host)
        if last_ts is not None:
            elapsed = now - last_ts
            if elapsed < self.min_interval_sec:
                self._sleep(self.min_interval_sec - elapsed)
        self._last_request_by_host[host] = self._clock()

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
    ) -> HttpResponse:
        """Perform an HTTP request with rate-limiting and retry logic."""
        last_exc: Exception | None = None
        total_attempts = self.max_retries + 1
        host = urlparse(url).netloc.lower()

        for attempt in range(total_attempts):
            self._respect_rate_limit(url)
            client = (
                self._get_ipv4_client()
                if (not self._has_custom_transport and host in self._ipv4_only_hosts)
                else self._client
            )
            try:
                resp = client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    data=data,
                )
                if resp.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                    delay = self.backoff_factor * (2**attempt)
                    self._sleep(delay)
                    continue
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                encoding, decoded_text = decode_html_bytes(resp.content, content_type=content_type)
                return HttpResponse(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    content=resp.content,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    encoding=encoding,
                    text=decoded_text,
                )
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if (
                    not self._has_custom_transport
                    and host
                    and host not in self._ipv4_only_hosts
                    and isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
                ):
                    self._ipv4_only_hosts.add(host)
                if isinstance(exc, httpx.HTTPStatusError):
                    if exc.response.status_code not in RETRYABLE_STATUS_CODES:
                        raise
                if attempt < self.max_retries:
                    delay = self.backoff_factor * (2**attempt)
                    self._sleep(delay)
                else:
                    raise

        assert last_exc is not None
        raise last_exc

    def get(self, url: str, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> HttpResponse:
        """Perform a GET request and return an HttpResponse."""
        return self.request("GET", url, headers=headers, params=params)

    def fetch_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        """Fetch URL and return decoded text."""
        return self.get(url, headers=headers).text

    def fetch_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        """Fetch URL and return raw bytes (for attachments)."""
        return self.get(url, headers=headers).content
