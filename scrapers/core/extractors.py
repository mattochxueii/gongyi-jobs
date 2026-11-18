"""Extract clean plain text and table text from HTML, PDF, .docx, and .xlsx.

Gracefully degrades with explicit diagnostic prompts when encountering legacy
binary formats (.doc, .xls, OLE2 compound documents, archives) or corrupted files.
"""

from __future__ import annotations

import importlib.util
import io
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Comment, Tag

_HAS_LXML = importlib.util.find_spec("lxml") is not None
_HAS_PDFPLUMBER = importlib.util.find_spec("pdfplumber") is not None
_HAS_DOCX = importlib.util.find_spec("docx") is not None
_HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

ATTACHMENT_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".doc": "doc",
    ".xls": "xls",
    ".wps": "wps",
    ".et": "et",
    ".zip": "zip",
    ".rar": "rar",
    ".7z": "7z",
    ".csv": "csv",
}

LEGACY_UNSUPPORTED_TYPES = {"doc", "xls", "wps", "et", "zip", "rar", "7z"}

_DATE_PATTERNS = [
    re.compile(r"(?:发布(?:日期|时间)|时间|日期|成文日期)[：:\s]*(\d{4})[-年/.](\d{1,2})[-月/.](\d{1,2})"),
    re.compile(r"\b(20\d{2})[-年/.](1[0-2]|0?[1-9])[-月/.](3[01]|[12]\d|0?[1-9])(?:日|\b)"),
]

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


@dataclass(slots=True)
class AttachmentLink:
    """Discovered attachment link from an announcement page."""

    name: str
    url: str
    file_type: str


@dataclass(slots=True)
class ExtractionResult:
    """Unified extraction result for an attachment or document."""

    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    supported: bool = True
    file_type: str = "unknown"


@dataclass(slots=True)
class HtmlExtractionResult:
    """Extraction result for an announcement HTML page."""

    title: str
    published_at: str | None
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    attachments: list[AttachmentLink] = field(default_factory=list)


def _normalize_whitespace(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = re.sub(r"[ \t\u00a0\u3000]+", " ", raw_line).strip()
        if cleaned:
            lines.append(cleaned)
        elif lines and lines[-1] != "":
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _format_table_rows(rows: list[list[str]]) -> str:
    formatted: list[str] = []
    for row in rows:
        cells = [re.sub(r"\s+", " ", (c or "").strip()) for c in row]
        if any(cells):
            formatted.append("| " + " | ".join(cells) + " |")
    return "\n".join(formatted)


_CONTENT_TYPE_EXT_MAP = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "text/csv": "csv",
    "application/csv": "csv",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
    "application/x-rar-compressed": "rar",
    "application/vnd.rar": "rar",
    "application/x-7z-compressed": "7z",
}

_ATTACHMENT_KEYWORDS = (
    "附件",
    "岗位表",
    "职位表",
    "计划表",
    "信息表",
    "登记表",
    "报名表",
    "一览表",
    "考试大纲",
    "下载",
)
_DYNAMIC_URL_HINTS = (
    "download",
    "down",
    "attach",
    "file",
    "appendix",
    "fujian",
    "servlet",
)
_DYNAMIC_QUERY_HINTS = ("fileurl=", "filename=", "fileid=", "attachid=", "fjid=", "down=")

DEFAULT_TITLE_SELECTORS: tuple[str, ...] = (
    "h1",
    ".hnks-wzbt",
    ".article-title",
    ".trs_title",
    ".news-title",
    ".title",
    "meta[name='ArticleTitle']",
    "title",
)

DEFAULT_CONTENT_SELECTORS: tuple[str, ...] = (
    ".article-content",
    ".trs_editor_view",
    ".TRS_UEDITOR",
    ".news-content",
    ".detail-content",
    ".details-box",
    ".hnks-wznr",
    "#zoom",
    "#content",
    "article",
    "main",
    ".content",
)


def extract_filename_from_content_disposition(content_disposition: str | None) -> str | None:
    """Extract filename from an HTTP Content-Disposition header (including RFC 5987)."""
    if not content_disposition:
        return None

    # RFC 5987: filename*=charset'lang'encoded-value
    m_ext = re.search(r"filename\*\s*=\s*([^;]+)", content_disposition, flags=re.IGNORECASE)
    if m_ext:
        raw_val = m_ext.group(1).strip().strip("\"'")
        if "''" in raw_val:
            charset, _, encoded = raw_val.partition("''")
            try:
                name = unquote(encoded, encoding=charset or "utf-8", errors="replace")
            except LookupError:
                name = unquote(encoded)
        else:
            name = unquote(raw_val)
        cleaned = Path(name.replace("\\", "/")).name.strip()
        if cleaned:
            return cleaned

    # Standard: filename="..." or filename=...
    m_std = re.search(r'filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;\s]+)', content_disposition, flags=re.IGNORECASE)
    if m_std:
        raw_name = (m_std.group(1) or m_std.group(2) or "").strip()
        # Handle RFC 2231 / percent-encoded fallback
        if raw_name.startswith("=?") or "%" in raw_name:
            raw_name = unquote(raw_name)
        cleaned = Path(raw_name.replace("\\", "/")).name.strip()
        if cleaned:
            return cleaned
    return None


def infer_file_type(filename_or_url: str) -> str:
    """Infer attachment file type from filename or URL path/query."""
    if not filename_or_url:
        return "other"
    parsed = urlparse(filename_or_url)
    candidates = [unquote(parsed.path.lower()), unquote(parsed.query.lower()), filename_or_url.lower()]
    for candidate in candidates:
        for ext, ftype in sorted(ATTACHMENT_EXTENSIONS.items(), key=lambda x: -len(x[0])):
            if candidate.endswith(ext) or f"{ext}&" in candidate or f"{ext}?" in candidate:
                return ftype
    return "other"


def infer_file_type_from_headers(
    content_type: str | None,
    content_disposition: str | None = None,
) -> str:
    """Infer attachment file type from HTTP Content-Disposition and Content-Type headers."""
    cd_name = extract_filename_from_content_disposition(content_disposition)
    if cd_name:
        ftype = infer_file_type(cd_name)
        if ftype != "other":
            return ftype

    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime in _CONTENT_TYPE_EXT_MAP:
            return _CONTENT_TYPE_EXT_MAP[mime]
    return "other"


def infer_file_type_from_bytes(raw: bytes, filename_hint: str = "") -> str:
    """Sniff attachment type from magic file headers when URL/headers lack extension."""
    if raw.startswith(b"%PDF-"):
        return "pdf"
    if raw.startswith(OLE2_MAGIC):
        hint = filename_hint.lower()
        if ".xls" in hint or any(k in filename_hint for k in ("岗位表", "职位表", "计划表", "信息表", "一览表")):
            return "xls"
        return "doc"
    if raw.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = set(zf.namelist())
            if "word/document.xml" in names:
                return "docx"
            if "xl/workbook.xml" in names:
                return "xlsx"
        except Exception:
            pass
        return "zip"
    return "other"


def _clean_title_text(raw_title: str) -> str:
    text = re.sub(r"<\s*br\s*/?\s*>", " ", raw_title or "", flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _is_placeholder_att_label(label: str) -> bool:
    stripped = re.sub(r"[\d.\s、:：()（）\[\]【】_-]+", "", label or "")
    return not stripped or label.strip() in {"下载", "点击下载", "附件", "附件下载"}


def _extract_date_from_soup_or_text(soup: BeautifulSoup, full_text: str) -> str | None:
    for meta_name in ("PubDate", "publishdate", "pubdate", "ArticleDate", "og:published_time"):
        meta = soup.find("meta", attrs={"name": re.compile(f"^{meta_name}$", re.I)}) or soup.find(
            "meta", attrs={"property": re.compile(f"^{meta_name}$", re.I)}
        )
        if meta and meta.get("content"):
            content = str(meta["content"]).strip()
            m = _DATE_PATTERNS[1].search(content)
            if m:
                return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    for pattern in _DATE_PATTERNS:
        m = pattern.search(full_text)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def extract_html(
    html: str,
    base_url: str = "",
    *,
    title_selectors: tuple[str, ...] | None = None,
    content_selectors: tuple[str, ...] | None = None,
) -> HtmlExtractionResult:
    """Extract clean title, publish date, main text, tables, and attachment links from HTML."""
    parser = "lxml" if _HAS_LXML else "html.parser"
    soup = BeautifulSoup(html or "", parser)

    # Remove noisy non-content elements
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag_name in ("script", "style", "noscript", "iframe", "svg", "canvas"):
        for node in soup.find_all(tag_name):
            node.decompose()

    # Extract title
    title = ""
    for selector in (title_selectors or DEFAULT_TITLE_SELECTORS):
        el = soup.select_one(selector)
        if el:
            candidate = el.get("content", "") if el.name == "meta" else el.get_text(" ", strip=True)
            candidate = _clean_title_text(str(candidate))
            if candidate:
                title = candidate
                break

    # Discover attachment links before mutating tables/navigation
    attachments: list[AttachmentLink] = []
    att_by_url: dict[str, AttachmentLink] = {}
    for a_tag in soup.find_all("a", href=True):
        raw_href = str(a_tag["href"]).strip()
        if not raw_href or raw_href.startswith(("javascript:", "mailto:", "#", "tel:")):
            continue
        resolved_url = urljoin(base_url, raw_href) if base_url else raw_href
        parsed_resolved = urlparse(resolved_url)
        if parsed_resolved.scheme and parsed_resolved.scheme.lower() not in {"http", "https"}:
            continue

        anchor_text = re.sub(r"\s+", " ", a_tag.get_text(" ", strip=True)).strip()
        title_attr = re.sub(r"\s+", " ", str(a_tag.get("title") or a_tag.get("download") or "")).strip()
        label = anchor_text or title_attr or Path(unquote(parsed_resolved.path)).name

        ftype = infer_file_type(resolved_url)
        if ftype == "other":
            ftype = infer_file_type(label)
        if ftype == "other" and title_attr:
            ftype = infer_file_type(title_attr)

        is_attachment_keyword = any(kw in (label + " " + title_attr) for kw in _ATTACHMENT_KEYWORDS)
        url_lower = resolved_url.lower()
        path_lower = unquote(parsed_resolved.path).lower()
        query_lower = unquote(parsed_resolved.query).lower()
        has_download_attr = a_tag.has_attr("download")
        has_dynamic_endpoint = any(tok in url_lower for tok in _DYNAMIC_URL_HINTS)
        has_file_query = any(qp in query_lower for qp in _DYNAMIC_QUERY_HINTS)
        is_obvious_html_page = path_lower.endswith((".html", ".htm", ".shtml")) and not has_file_query and not has_download_attr

        is_match = (
            ftype != "other"
            or has_download_attr
            or (not is_obvious_html_page and (has_file_query or (is_attachment_keyword and has_dynamic_endpoint)))
        )
        if is_match:
            existing = att_by_url.get(resolved_url)
            if existing is None:
                att_link = AttachmentLink(
                    name=label or "未命名附件",
                    url=resolved_url,
                    file_type=ftype,
                )
                att_by_url[resolved_url] = att_link
                attachments.append(att_link)
            else:
                if _is_placeholder_att_label(existing.name) and label and not _is_placeholder_att_label(label):
                    existing.name = label
                if existing.file_type == "other" and ftype != "other":
                    existing.file_type = ftype

    full_page_text = soup.get_text(" ", strip=True)
    published_at = _extract_date_from_soup_or_text(soup, full_page_text)

    # Locate main content container or fallback to body
    content_root: Tag | BeautifulSoup = soup
    for selector in (content_selectors or DEFAULT_CONTENT_SELECTORS):
        found = soup.select_one(selector)
        if isinstance(found, Tag):
            txt_len = len(found.get_text(strip=True))
            if txt_len >= 20 or (txt_len >= 4 and found.find("a", href=True)):
                content_root = found
                break
    else:
        for nav_tag in soup.find_all(["nav", "header", "footer"]):
            nav_tag.decompose()
        if soup.body:
            content_root = soup.body

    # Extract HTML tables and replace them inline with pipe-separated text
    extracted_tables: list[list[list[str]]] = []
    for tbl in content_root.find_all("table"):
        rows_data: list[list[str]] = []
        for tr in tbl.find_all("tr"):
            cells = [re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip() for cell in tr.find_all(["th", "td"])]
            if any(cells):
                rows_data.append(cells)
        if rows_data:
            extracted_tables.append(rows_data)
            table_text_block = "\n" + _format_table_rows(rows_data) + "\n"
            tbl.replace_with(table_text_block)

    for br in content_root.find_all("br"):
        br.replace_with("\n")
    for block_tag in content_root.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4"]):
        block_tag.append("\n")

    clean_text = _normalize_whitespace(content_root.get_text())
    return HtmlExtractionResult(
        title=title,
        published_at=published_at,
        text=clean_text,
        tables=extracted_tables,
        attachments=attachments,
    )


def _read_bytes(source: bytes | Path) -> bytes:
    if isinstance(source, Path):
        return source.read_bytes()
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    raise TypeError(f"Expected bytes or Path, got {type(source)!r}")


def _make_legacy_degradation_result(file_type: str, display_name: str, reason: str | None = None) -> ExtractionResult:
    detail = reason or f"旧版二进制或压缩格式 (.{file_type}) 暂不支持直接无损解析"
    notice = (
        f"[附件解析降级提示] 检测到附件《{display_name}》(格式: {file_type})：{detail}。"
        "请通过公告原链接下载查阅或由人工核验岗位表。"
    )
    return ExtractionResult(
        text=notice,
        tables=[],
        warnings=[f"Graceful degradation for {display_name} ({file_type}): {detail}"],
        supported=False,
        file_type=file_type,
    )


def extract_docx(source: bytes | Path, display_name: str = "document.docx") -> ExtractionResult:
    """Extract paragraphs and tables from a .docx file."""
    raw = _read_bytes(source)
    if raw.startswith(OLE2_MAGIC):
        return _make_legacy_degradation_result(
            "doc",
            display_name,
            "文件头为 OLE2 二进制格式（实为旧版 .doc 伪装为 .docx）",
        )

    if _HAS_DOCX:
        try:
            import docx  # type: ignore[import-not-found]

            doc = docx.Document(io.BytesIO(raw))
            blocks: list[str] = []
            tables: list[list[list[str]]] = []
            for p in doc.paragraphs:
                txt = p.text.strip()
                if txt:
                    blocks.append(txt)
            for tbl in doc.tables:
                t_rows: list[list[str]] = []
                for row in tbl.rows:
                    t_rows.append([cell.text.strip() for cell in row.cells])
                if t_rows:
                    tables.append(t_rows)
                    blocks.append(_format_table_rows(t_rows))
            return ExtractionResult(
                text=_normalize_whitespace("\n\n".join(blocks)),
                tables=tables,
                warnings=[],
                supported=True,
                file_type="docx",
            )
        except Exception as exc:
            return ExtractionResult(
                text=f"[附件解析异常] 无法解析 DOCX 附件《{display_name}》: {exc}",
                tables=[],
                warnings=[f"Failed to parse docx {display_name}: {exc}"],
                supported=False,
                file_type="docx",
            )

    # Pure-Python standard library .docx (OpenXML ZIP) parser
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            if "word/document.xml" not in zf.namelist():
                raise ValueError("Missing word/document.xml in archive")
            xml_bytes = zf.read("word/document.xml")

        root = ET.fromstring(xml_bytes)
        body = root.find(f"{_WORD_NS}body")
        if body is None:
            body = root

        blocks = []
        tables = []

        for child in body:
            if child.tag == f"{_WORD_NS}p":
                para_text = "".join(t.text or "" for t in child.iter(f"{_WORD_NS}t")).strip()
                if para_text:
                    blocks.append(para_text)
            elif child.tag == f"{_WORD_NS}tbl":
                t_rows = []
                for tr in child.findall(f".//{_WORD_NS}tr"):
                    row_cells: list[str] = []
                    for tc in tr.findall(f"./{_WORD_NS}tc"):
                        cell_paras: list[str] = []
                        for p in tc.findall(f".//{_WORD_NS}p"):
                            p_txt = "".join(t.text or "" for t in p.iter(f"{_WORD_NS}t")).strip()
                            if p_txt:
                                cell_paras.append(p_txt)
                        row_cells.append(" ".join(cell_paras))
                    if any(row_cells):
                        t_rows.append(row_cells)
                if t_rows:
                    tables.append(t_rows)
                    blocks.append(_format_table_rows(t_rows))

        return ExtractionResult(
            text=_normalize_whitespace("\n\n".join(blocks)),
            tables=tables,
            warnings=[],
            supported=True,
            file_type="docx",
        )
    except Exception as exc:
        return ExtractionResult(
            text=f"[附件解析异常] 无法解析 DOCX 附件《{display_name}》: {exc}",
            tables=[],
            warnings=[f"Failed to parse docx {display_name}: {exc}"],
            supported=False,
            file_type="docx",
        )


def _col_letters_to_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    if not letters:
        return 0
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def extract_xlsx(source: bytes | Path, display_name: str = "workbook.xlsx") -> ExtractionResult:
    """Extract sheet names and tabular rows from an .xlsx file."""
    raw = _read_bytes(source)
    if raw.startswith(OLE2_MAGIC):
        return _make_legacy_degradation_result(
            "xls",
            display_name,
            "文件头为 OLE2 二进制格式（实为旧版 .xls 伪装为 .xlsx）",
        )

    if _HAS_OPENPYXL:
        try:
            import openpyxl  # type: ignore[import-not-found]

            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            blocks: list[str] = []
            tables: list[list[list[str]]] = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                sheet_rows: list[list[str]] = []
                for row in ws.iter_rows(values_only=True):
                    cells = ["" if v is None else str(v).strip() for v in row]
                    if any(cells):
                        sheet_rows.append(cells)
                if sheet_rows:
                    tables.append(sheet_rows)
                    blocks.append(f"[工作表: {sheet_name}]\n" + _format_table_rows(sheet_rows))
            wb.close()
            return ExtractionResult(
                text=_normalize_whitespace("\n\n".join(blocks)),
                tables=tables,
                warnings=[],
                supported=True,
                file_type="xlsx",
            )
        except Exception as exc:
            return ExtractionResult(
                text=f"[附件解析异常] 无法解析 XLSX 附件《{display_name}》: {exc}",
                tables=[],
                warnings=[f"Failed to parse xlsx {display_name}: {exc}"],
                supported=False,
                file_type="xlsx",
            )

    # Pure-Python standard library .xlsx (OpenXML SpreadsheetML ZIP) parser
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in ss_root.findall(f"{_SHEET_NS}si"):
                    text_parts = [t.text or "" for t in si.iter(f"{_SHEET_NS}t")]
                    shared_strings.append("".join(text_parts))

            # Map sheet names to target XML paths
            sheet_entries: list[tuple[str, str]] = []
            if "xl/workbook.xml" in names:
                wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
                rels_map: dict[str, str] = {}
                if "xl/_rels/workbook.xml.rels" in names:
                    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
                    for rel in rels_root.findall(f"{_PKG_REL_NS}Relationship"):
                        r_id = rel.attrib.get("Id", "")
                        target = rel.attrib.get("Target", "").lstrip("/")
                        if not target.startswith("xl/"):
                            target = f"xl/{target}"
                        rels_map[r_id] = target

                for idx, sh in enumerate(wb_root.findall(f".//{_SHEET_NS}sheet"), start=1):
                    s_name = sh.attrib.get("name", f"Sheet{idx}")
                    r_id = sh.attrib.get(f"{_REL_NS}id", "")
                    target_path = rels_map.get(r_id, f"xl/worksheets/sheet{idx}.xml")
                    if target_path in names:
                        sheet_entries.append((s_name, target_path))

            if not sheet_entries:
                for name in sorted(names):
                    if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                        sheet_entries.append((Path(name).stem, name))

            blocks = []
            tables = []
            for sheet_name, sheet_path in sheet_entries:
                ws_root = ET.fromstring(zf.read(sheet_path))
                sheet_rows = []
                for row_el in ws_root.findall(f".//{_SHEET_NS}row"):
                    cells_by_col: dict[int, str] = {}
                    next_col = 0
                    for c_el in row_el.findall(f"{_SHEET_NS}c"):
                        ref = c_el.attrib.get("r", "")
                        col_idx = _col_letters_to_index(ref) if ref else next_col
                        next_col = col_idx + 1
                        c_type = c_el.attrib.get("t", "")
                        val_str = ""
                        if c_type == "inlineStr":
                            val_str = "".join(t.text or "" for t in c_el.iter(f"{_SHEET_NS}t"))
                        else:
                            v_el = c_el.find(f"{_SHEET_NS}v")
                            raw_val = (v_el.text or "") if v_el is not None else ""
                            if c_type == "s" and raw_val.isdigit():
                                s_idx = int(raw_val)
                                if 0 <= s_idx < len(shared_strings):
                                    val_str = shared_strings[s_idx]
                            else:
                                val_str = raw_val
                        cells_by_col[col_idx] = val_str.strip()

                    if cells_by_col:
                        max_col = max(cells_by_col.keys())
                        row_list = [cells_by_col.get(i, "") for i in range(max_col + 1)]
                        if any(row_list):
                            sheet_rows.append(row_list)

                if sheet_rows:
                    tables.append(sheet_rows)
                    blocks.append(f"[工作表: {sheet_name}]\n" + _format_table_rows(sheet_rows))

            return ExtractionResult(
                text=_normalize_whitespace("\n\n".join(blocks)),
                tables=tables,
                warnings=[],
                supported=True,
                file_type="xlsx",
            )
    except Exception as exc:
        return ExtractionResult(
            text=f"[附件解析异常] 无法解析 XLSX 附件《{display_name}》: {exc}",
            tables=[],
            warnings=[f"Failed to parse xlsx {display_name}: {exc}"],
            supported=False,
            file_type="xlsx",
        )


def _decompress_pdf_stream(stream_bytes: bytes) -> bytes:
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return zlib.decompress(stream_bytes, wbits)
        except zlib.error:
            continue
    return stream_bytes


def _parse_tounicode_cmap(cmap_text: str) -> tuple[dict[int, str], int]:
    """Parse a PDF ToUnicode CMap stream into {cid_int: unicode_str} and byte width."""
    mapping: dict[int, str] = {}
    code_width = 2

    cs_match = re.search(
        r"begincodespacerange\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
        cmap_text,
        flags=re.IGNORECASE,
    )
    if cs_match:
        code_width = max(1, len(cs_match.group(1)) // 2)

    def _hex_to_utf16(hex_val: str) -> str:
        clean = re.sub(r"\s+", "", hex_val)
        if len(clean) % 2 == 1:
            clean += "0"
        try:
            return bytes.fromhex(clean).decode("utf-16-be", errors="ignore")
        except ValueError:
            return ""

    for blk in re.findall(r"beginbfchar\b(.*?)endbfchar", cmap_text, flags=re.DOTALL):
        for src_hex, dst_hex in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            code_width = max(1, len(src_hex) // 2)
            mapping[int(src_hex, 16)] = _hex_to_utf16(dst_hex)

    for blk in re.findall(r"beginbfrange\b(.*?)endbfrange", cmap_text, flags=re.DOTALL):
        for line in blk.splitlines():
            line = line.strip()
            if not line:
                continue
            m_arr = re.match(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", line)
            if m_arr:
                start = int(m_arr.group(1), 16)
                end = int(m_arr.group(2), 16)
                dsts = re.findall(r"<([0-9A-Fa-f]+)>", m_arr.group(3))
                for offset, dst_hex in enumerate(dsts):
                    if start + offset <= end:
                        mapping[start + offset] = _hex_to_utf16(dst_hex)
                continue
            m_seq = re.match(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", line)
            if m_seq:
                start = int(m_seq.group(1), 16)
                end = int(m_seq.group(2), 16)
                dst_start = int(m_seq.group(3), 16)
                for offset in range(max(0, min(end - start + 1, 4096))):
                    try:
                        mapping[start + offset] = chr(dst_start + offset)
                    except ValueError:
                        break
    return mapping, code_width


def _extract_pdf_font_cmaps(raw: bytes) -> tuple[dict[int, tuple[dict[int, str], int]], dict[str, dict[int, tuple[dict[int, str], int]]]]:
    """Build per-content-stream and global font-name -> ToUnicode CMap tables from PDF objects."""
    stream_by_obj: dict[int, bytes] = {}
    obj_headers: dict[int, bytes] = {}
    for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", raw, flags=re.DOTALL):
        obj_id = int(m.group(1))
        body = m.group(2)
        obj_headers[obj_id] = body
        sm = re.search(rb"stream\r?\n(.*?)\r?\nendstream", body, flags=re.DOTALL)
        if sm:
            stream_by_obj[obj_id] = _decompress_pdf_stream(sm.group(1))

    cmap_by_obj: dict[int, tuple[dict[int, str], int]] = {}
    for obj_id, st in stream_by_obj.items():
        if b"beginbfchar" in st or b"beginbfrange" in st:
            cmap_by_obj[obj_id] = _parse_tounicode_cmap(st.decode("latin-1", errors="ignore"))

    font_obj_to_cmap: dict[int, tuple[dict[int, str], int]] = {}
    for obj_id, body in obj_headers.items():
        tm = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", body)
        if tm:
            cmap_obj = int(tm.group(1))
            if cmap_obj in cmap_by_obj:
                font_obj_to_cmap[obj_id] = cmap_by_obj[cmap_obj]

    # Map content stream object IDs and font resource names to their CMap
    content_cmaps: dict[int, dict[str, tuple[dict[int, str], int]]] = {}
    global_font_cmaps: dict[str, tuple[dict[int, str], int]] = {}
    for body in obj_headers.values():
        fm = re.search(rb"/Font\s*<<(.*?)>>", body, flags=re.DOTALL)
        if not fm:
            continue
        page_fonts: dict[str, tuple[dict[int, str], int]] = {}
        for ref_m in re.finditer(rb"/([A-Za-z0-9_.-]+)\s+(\d+)\s+\d+\s+R", fm.group(1)):
            fname = ref_m.group(1).decode("ascii", errors="ignore")
            fobj = int(ref_m.group(2))
            if fobj in font_obj_to_cmap:
                page_fonts[fname] = font_obj_to_cmap[fobj]
                global_font_cmaps.setdefault(fname, font_obj_to_cmap[fobj])
        if page_fonts:
            cm_single = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", body)
            if cm_single:
                content_cmaps[int(cm_single.group(1))] = page_fonts
            cm_arr = re.search(rb"/Contents\s*\[(.*?)\]", body, flags=re.DOTALL)
            if cm_arr:
                for cref in re.findall(rb"(\d+)\s+\d+\s+R", cm_arr.group(1)):
                    content_cmaps[int(cref)] = page_fonts

    return global_font_cmaps, content_cmaps


def _decode_pdf_hex_with_cmap(hex_token: str, cmap_info: tuple[dict[int, str], int]) -> str:
    cmap, width = cmap_info
    hex_str = re.sub(r"\s+", "", hex_token[1:-1])
    step = width * 2
    out: list[str] = []
    for idx in range(0, len(hex_str) - step + 1, step):
        code = int(hex_str[idx : idx + step], 16)
        if code in cmap:
            out.append(cmap[code])
    return "".join(out)


def _decode_pdf_literal_token(token: str) -> str:
    """Decode a PDF string token (...) or hex token <...>."""
    token = token.strip()
    if token.startswith("<") and token.endswith(">"):
        hex_str = re.sub(r"\s+", "", token[1:-1])
        if len(hex_str) % 2 == 1:
            hex_str += "0"
        try:
            raw_bytes = bytes.fromhex(hex_str)
            if raw_bytes.startswith(b"\xfe\xff"):
                return raw_bytes[2:].decode("utf-16-be", errors="ignore")
            if b"\x00" in raw_bytes:
                return ""
            try:
                return raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return raw_bytes.decode("gb18030", errors="ignore")
        except ValueError:
            return ""
    if token.startswith("(") and token.endswith(")"):
        inner = token[1:-1]
        inner = (
            inner.replace(r"\n", "\n")
            .replace(r"\r", "\r")
            .replace(r"\t", "\t")
            .replace(r"\(", "(")
            .replace(r"\)", ")")
            .replace(r"\\", "\\")
        )
        return inner
    return ""


def _strip_control_chars(text: str) -> str:
    return "".join(ch for ch in text if ch in "\n\r\t" or ord(ch) >= 32)


def _is_readable_pdf_text(text: str, raw_size: int) -> bool:
    if not text.strip():
        return False
    cleaned = re.sub(r"\s+", "", text)
    if not cleaned:
        return False
    meaningful = sum(1 for ch in cleaned if ("\u4e00" <= ch <= "\u9fff") or ch.isalnum())
    if raw_size > 4096 and meaningful < 15:
        return False
    return meaningful / len(cleaned) >= 0.25


def extract_pdf(source: bytes | Path, display_name: str = "document.pdf") -> ExtractionResult:
    """Extract plain text and tables from a PDF file."""
    raw = _read_bytes(source)
    if not raw.startswith(b"%PDF-"):
        return ExtractionResult(
            text=f"[附件解析异常] 附件《{display_name}》不是有效的 PDF 文件。",
            tables=[],
            warnings=[f"Invalid PDF header in {display_name}"],
            supported=False,
            file_type="pdf",
        )

    if _HAS_PDFPLUMBER:
        try:
            import pdfplumber  # type: ignore[import-not-found]

            blocks: list[str] = []
            tables: list[list[list[str]]] = []
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for page in pdf.pages:
                    page_text = (page.extract_text() or "").strip()
                    if page_text:
                        blocks.append(page_text)
                    for tbl in page.extract_tables() or []:
                        cleaned_rows = [
                            [re.sub(r"\s+", " ", (c or "").strip()) for c in row]
                            for row in tbl
                            if row and any(c and str(c).strip() for c in row)
                        ]
                        if cleaned_rows:
                            tables.append(cleaned_rows)
                            blocks.append(_format_table_rows(cleaned_rows))
            return ExtractionResult(
                text=_normalize_whitespace("\n\n".join(blocks)),
                tables=tables,
                warnings=[],
                supported=True,
                file_type="pdf",
            )
        except Exception as exc:
            return ExtractionResult(
                text=f"[附件解析异常] 无法解析 PDF 附件《{display_name}》: {exc}",
                tables=[],
                warnings=[f"Failed to parse pdf {display_name}: {exc}"],
                supported=False,
                file_type="pdf",
            )

    # Pure-Python stream/operator fallback for PDF text extraction (with ToUnicode CMap support)
    global_font_cmaps, content_cmaps = _extract_pdf_font_cmaps(raw)
    uses_cid_identity = b"/Identity-H" in raw or b"/CIDFont" in raw
    obj_stream_pattern = re.compile(rb"(?:(\d+)\s+\d+\s+obj\b.*?)?stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
    op_pattern = re.compile(r"(/([A-Za-z0-9_.-]+)\s+[\d.]+\s+Tf|\([^()\\]*(?:\\.[^()\\]*)*\)|<[0-9A-Fa-f\s]+>)")
    lines: list[str] = []
    tables: list[list[list[str]]] = []

    for match in obj_stream_pattern.finditer(raw):
        obj_id = int(match.group(1)) if match.group(1) else None
        decompressed = _decompress_pdf_stream(match.group(2))
        if b"beginbfchar" in decompressed or b"beginbfrange" in decompressed:
            continue

        try:
            stream_str = decompressed.decode("utf-8")
        except UnicodeDecodeError:
            stream_str = decompressed.decode("latin-1", errors="ignore")

        page_cmaps = content_cmaps.get(obj_id, {}) if obj_id is not None else {}
        current_font: str | None = None

        for bt_block in re.findall(r"BT\b(.*?)ET\b", stream_str, flags=re.DOTALL):
            parts: list[str] = []
            for m_op in op_pattern.finditer(bt_block):
                tok = m_op.group(1)
                font_name = m_op.group(2)
                if font_name:
                    current_font = font_name
                    continue
                if tok.startswith("<") and tok.endswith(">"):
                    cmap_info = page_cmaps.get(current_font or "") or global_font_cmaps.get(current_font or "")
                    if cmap_info is not None:
                        decoded = _decode_pdf_hex_with_cmap(tok, cmap_info)
                    elif uses_cid_identity and not tok[1:].strip().upper().startswith("FEFF"):
                        decoded = ""
                    else:
                        decoded = _decode_pdf_literal_token(tok)
                else:
                    decoded = _decode_pdf_literal_token(tok)
                if decoded:
                    parts.append(_strip_control_chars(decoded))
            if parts:
                line_str = "".join(parts).strip()
                if line_str:
                    lines.append(line_str)

    joined_text = _normalize_whitespace("\n".join(lines))
    if not _is_readable_pdf_text(joined_text, len(raw)):
        return ExtractionResult(
            text=(
                f"[附件解析降级提示] 检测到 PDF 附件《{display_name}》使用扫描件或特殊字形编码"
                "（当前未启用 pdfplumber 或缺少可映射文本层），请通过附件原链接下载查阅。"
            ),
            tables=[],
            warnings=[f"Graceful degradation for PDF {display_name}: unreadable or image-only PDF stream"],
            supported=False,
            file_type="pdf",
        )

    # Detect pipe-delimited table rows inside extracted PDF text
    current_table: list[list[str]] = []
    for ln in joined_text.split("\n"):
        if "|" in ln:
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if len(cells) >= 2 and any(cells):
                current_table.append(cells)
                continue
        if current_table:
            tables.append(current_table)
            current_table = []
    if current_table:
        tables.append(current_table)

    return ExtractionResult(
        text=joined_text,
        tables=tables,
        warnings=[],
        supported=True,
        file_type="pdf",
    )


def extract_attachment(
    source: bytes | Path,
    filename_or_url: str,
    *,
    file_type: str | None = None,
    display_name: str | None = None,
    content_disposition: str | None = None,
    content_type: str | None = None,
) -> ExtractionResult:
    """Dispatch attachment extraction by file type with graceful degradation for legacy formats."""
    cd_filename = extract_filename_from_content_disposition(content_disposition)
    url_filename = Path(unquote(urlparse(str(filename_or_url)).path)).name
    ftype = file_type if file_type and file_type != "other" else infer_file_type(filename_or_url)
    if ftype == "other":
        ftype = infer_file_type_from_headers(content_type, content_disposition)
    if ftype == "other" and display_name:
        ftype = infer_file_type(display_name)
    if ftype == "other" and isinstance(source, Path):
        ftype = infer_file_type(source.name)

    resolved_name = display_name or cd_filename or url_filename or str(filename_or_url)
    if not display_name:
        if "." not in url_filename and cd_filename:
            resolved_name = cd_filename
        elif url_filename and ftype != "other" and not url_filename.lower().endswith(f".{ftype}") and cd_filename:
            resolved_name = cd_filename

    if ftype in LEGACY_UNSUPPORTED_TYPES:
        return _make_legacy_degradation_result(ftype, resolved_name)

    raw = _read_bytes(source)
    if ftype == "other":
        ftype = infer_file_type_from_bytes(raw, filename_hint=resolved_name)
        if ftype in LEGACY_UNSUPPORTED_TYPES:
            return _make_legacy_degradation_result(ftype, resolved_name)

    if raw.startswith(OLE2_MAGIC):
        legacy_kind = "xls" if ftype == "xlsx" else "doc"
        return _make_legacy_degradation_result(
            legacy_kind,
            resolved_name,
            f"检测到旧版二进制 OLE2 复合文档格式 (.{legacy_kind})",
        )

    if ftype == "pdf":
        return extract_pdf(raw, display_name=resolved_name)
    if ftype == "docx":
        return extract_docx(raw, display_name=resolved_name)
    if ftype == "xlsx":
        return extract_xlsx(raw, display_name=resolved_name)

    return _make_legacy_degradation_result(
        ftype,
        resolved_name,
        f"暂不支持直接解析此附件类型 ({ftype})",
    )
