"""Helpers to generate valid local .docx, .xlsx, .pdf, and legacy .doc/.xls binary test fixtures."""

from __future__ import annotations

import io
import zipfile
import zlib
from pathlib import Path

from scrapers.core.extractors import OLE2_MAGIC


def build_sample_docx_bytes() -> bytes:
    """Create a minimal valid OpenXML .docx containing paragraphs and a recruitment table."""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>附件2：2026年河南省直事业单位公开招聘报考指南</w:t></w:r></w:p>
    <w:p><w:r><w:t>一、报考人员须具备本科及以上学历并取得相应学位。</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>岗位代码</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>岗位名称</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>岗位层级</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>0101</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>信息技术岗</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>专业技术岗（初级）</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def build_sample_xlsx_bytes() -> bytes:
    """Create a minimal valid OpenXML .xlsx containing a job position table."""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""

    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="省直岗位表" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""

    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

    shared_strings_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="10" uniqueCount="10">
  <si><t>单位名称</t></si>
  <si><t>岗位代码</t></si>
  <si><t>岗位名称</t></si>
  <si><t>招聘人数</t></si>
  <si><t>最低学历</t></si>
  <si><t>专业要求</t></si>
  <si><t>河南省某直属事业单位</t></si>
  <si><t>0101</t></si>
  <si><t>信息技术岗</t></si>
  <si><t>本科</t></si>
</sst>"""

    sheet1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="s"><v>0</v></c>
      <c r="B1" t="s"><v>1</v></c>
      <c r="C1" t="s"><v>2</v></c>
      <c r="D1" t="s"><v>3</v></c>
      <c r="E1" t="s"><v>4</v></c>
      <c r="F1" t="s"><v>5</v></c>
    </row>
    <row r="2">
      <c r="A2" t="s"><v>6</v></c>
      <c r="B2" t="s"><v>7</v></c>
      <c r="C2" t="s"><v>8</v></c>
      <c r="D2"><v>2</v></c>
      <c r="E2" t="s"><v>9</v></c>
      <c r="F2" t="inlineStr"><is><t>计算机科学与技术、软件工程</t></is></c>
    </row>
  </sheetData>
</worksheet>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/sharedStrings.xml", shared_strings_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1_xml)
    return buf.getvalue()


def build_sample_pdf_bytes() -> bytes:
    """Create a valid PDF-1.4 document with FlateDecode text and table rows."""
    lines = [
        "附件3：2026年河南省直属事业单位笔试考场规则",
        "| 科目代码 | 科目名称 | 考试时长 |",
        "| 101 | 职业能力测验 | 90分钟 |",
        "| 102 | 公共基础知识 | 120分钟 |",
    ]
    bt_blocks: list[str] = []
    for line in lines:
        utf16_hex = "FEFF" + line.encode("utf-16-be").hex().upper()
        bt_blocks.append(f"BT /F1 12 Tf 72 720 Td <{utf16_hex}> Tj ET")

    stream_text = "\n".join(bt_blocks).encode("ascii")
    compressed = zlib.compress(stream_text)

    pdf_parts = [
        b"%PDF-1.4\n",
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj\n",
        f"4 0 obj << /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode("ascii"),
        compressed,
        b"\nendstream\nendobj\n",
        b"xref\n0 5\n0000000000 65535 f \n",
        b"trailer << /Size 5 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n",
    ]
    return b"".join(pdf_parts)


def build_legacy_ole2_bytes() -> bytes:
    """Create mock legacy OLE2 binary header (.doc / .xls)."""
    return OLE2_MAGIC + b"\x00" * 128


def ensure_binary_fixtures(fixtures_dir: Path) -> dict[str, Path]:
    """Ensure sample .docx, .xlsx, .pdf, .doc, and .xls files exist in fixtures_dir."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "docx": fixtures_dir / "henan_2026_guide.docx",
        "xlsx": fixtures_dir / "henan_2026_posts.xlsx",
        "pdf": fixtures_dir / "henan_2026_notice.pdf",
        "doc": fixtures_dir / "legacy_apply_form.doc",
        "xls": fixtures_dir / "legacy_posts_old.xls",
    }
    paths["docx"].write_bytes(build_sample_docx_bytes())
    paths["xlsx"].write_bytes(build_sample_xlsx_bytes())
    paths["pdf"].write_bytes(build_sample_pdf_bytes())
    paths["doc"].write_bytes(build_legacy_ole2_bytes())
    paths["xls"].write_bytes(build_legacy_ole2_bytes())
    return paths
