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
    re.compile(r"\b(20\d{2})[-年/.](0?[1-9]|1[0-2])[-月/.](0?[1-9]|[12]\d|3[01])(?:日|\b)"),
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


def extract_html(html: str, base_url: str = "") -> HtmlExtractionResult:
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
    for selector in (
        "h1",
        ".article-title",
        ".trs_title",
        ".news-title",
        ".title",
        "meta[name='ArticleTitle']",
        "title",
    ):
        el = soup.select_one(selector)
        if el:
            candidate = el.get("content", "") if el.name == "meta" else el.get_text(" ", strip=True)
            candidate = re.sub(r"\s+", " ", str(candidate)).strip()
            if candidate:
                title = candidate
                break

    # Discover attachment links before mutating tables/navigation
    attachments: list[AttachmentLink] = []
    seen_att_urls: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        raw_href = str(a_tag["href"]).strip()
        if not raw_href or raw_href.startswith(("javascript:", "mailto:", "#")):
            continue
        resolved_url = urljoin(base_url, raw_href) if base_url else raw_href
        anchor_text = re.sub(r"\s+", " ", a_tag.get_text(" ", strip=True)).strip()
        title_attr = re.sub(r"\s+", " ", str(a_tag.get("title") or a_tag.get("download") or "")).strip()
        label = anchor_text or title_attr or Path(unquote(urlparse(resolved_url).path)).name

        ftype = infer_file_type(resolved_url)
        if ftype == "other":
            ftype = infer_file_type(label)
        is_attachment_keyword = any(
            kw in (label + " " + title_attr) for kw in ("附件", "岗位表", "职位表", "报名表", "下载", "一览表")
        )
        if ftype != "other" or (is_attachment_keyword and any(ext in resolved_url.lower() for ext in ("download", "attach", "file", " appendix"))):
            if resolved_url not in seen_att_urls:
                seen_att_urls.add(resolved_url)
                attachments.append(
                    AttachmentLink(
                        name=label or "未命名附件",
                        url=resolved_url,
                        file_type=ftype,
                    )
                )

    full_page_text = soup.get_text(" ", strip=True)
    published_at = _extract_date_from_soup_or_text(soup, full_page_text)

    # Locate main content container or fallback to body
    content_root: Tag | BeautifulSoup = soup
    for selector in (
        ".article-content",
        ".trs_editor_view",
        ".news-content",
        ".detail-content",
        "#zoom",
        "article",
        "main",
        ".content",
    ):
        found = soup.select_one(selector)
        if isinstance(found, Tag) and len(found.get_text(strip=True)) >= 20:
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

    # Pure-Python stream/operator fallback for PDF text extraction
    stream_pattern = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
    token_pattern = re.compile(r"(\([^()\\]*(?:\\.[^()\\]*)*\)|<[0-9A-Fa-f\s]+>)")
    lines: list[str] = []
    tables: list[list[list[str]]] = []

    for match in stream_pattern.finditer(raw):
        stream_bytes = match.group(1)
        decompressed = stream_bytes
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decompressed = zlib.decompress(stream_bytes, wbits)
                break
            except zlib.error:
                continue

        try:
            stream_str = decompressed.decode("utf-8")
        except UnicodeDecodeError:
            stream_str = decompressed.decode("latin-1", errors="ignore")

        for bt_block in re.findall(r"BT\b(.*?)ET\b", stream_str, flags=re.DOTALL):
            parts: list[str] = []
            for tok in token_pattern.findall(bt_block):
                decoded = _decode_pdf_literal_token(tok)
                if decoded:
                    parts.append(decoded)
            if parts:
                line_str = "".join(parts).strip()
                if line_str:
                    lines.append(line_str)

    # Detect pipe-delimited table rows inside extracted PDF text
    current_table: list[list[str]] = []
    for ln in lines:
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
        text=_normalize_whitespace("\n".join(lines)),
        tables=tables,
        warnings=[],
        supported=True,
        file_type="pdf",
    )


def extract_attachment(source: bytes | Path, filename_or_url: str) -> ExtractionResult:
    """Dispatch attachment extraction by file type with graceful degradation for legacy formats."""
    display_name = Path(unquote(urlparse(str(filename_or_url)).path)).name or str(filename_or_url)
    ftype = infer_file_type(filename_or_url)
    if ftype == "other" and isinstance(source, Path):
        ftype = infer_file_type(source.name)

    if ftype in LEGACY_UNSUPPORTED_TYPES:
        return _make_legacy_degradation_result(ftype, display_name)

    raw = _read_bytes(source)
    if raw.startswith(OLE2_MAGIC):
        legacy_kind = "xls" if ftype == "xlsx" else "doc"
        return _make_legacy_degradation_result(
            legacy_kind,
            display_name,
            f"检测到旧版二进制 OLE2 复合文档格式 (.{legacy_kind})",
        )

    if ftype == "pdf":
        return extract_pdf(raw, display_name=display_name)
    if ftype == "docx":
        return extract_docx(raw, display_name=display_name)
    if ftype == "xlsx":
        return extract_xlsx(raw, display_name=display_name)

    return _make_legacy_degradation_result(
        ftype,
        display_name,
        f"暂不支持直接解析此附件类型 ({ftype})",
    )
