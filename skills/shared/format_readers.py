"""阶段 5 的多格式确定性读取。

只用标准库，从常用办公格式中提取可读文本，不安装依赖、不调用大模型、
不做模糊推断：任何无法确定解析的内容都按失败处理，由上层写入 02 异常。

支持格式：
- DOCX / PPTX / XLSX：OOXML 是 ZIP + XML，按规范位置做确定性文本提取；
- HTML / HTM：剥离脚本与样式后按块级标签分行；
- JSON：解析后以规范化缩进重排；
- PDF：原则支持，但依赖宿主环境提供的可选依赖 pypdf；缺失时抛出
  DependencyMissing，由阶段 5 按 format_unsupported 准确停止。

旧版二进制 Office（.doc/.xls/.ppt）、图片、音视频不在本模块职责内，
继续由阶段 5 的格式白名单拒绝。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_READERS = {
    ".docx": "_read_docx",
    ".pptx": "_read_pptx",
    ".xlsx": "_read_xlsx",
    ".html": "_read_html",
    ".htm": "_read_html",
    ".json": "_read_json",
    ".pdf": "_read_pdf",
}


class DependencyMissing(Exception):
    """格式在原则上受支持，但当前环境缺少可选依赖（如 pypdf）。"""


def readable_text(payload: bytes, suffix: str) -> str:
    """按扩展名把原始字节确定性转换为可读 Markdown 文本。"""
    reader_name = _READERS.get(suffix)
    if reader_name is None:
        raise ValueError(f"no deterministic reader for suffix: {suffix}")
    reader = globals()[reader_name]
    return reader(payload)


def _finalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or "\x00" in text:
        raise ValueError("empty or binary extraction result")
    return text.rstrip() + "\n"


def _markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    escaped = [[cell.replace("|", "\\|").replace("\n", " ") for cell in row] for row in padded]
    lines = ["| " + " | ".join(escaped[0]) + " |", "| " + " | ".join("---" for _ in escaped[0]) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in escaped[1:])
    return "\n".join(lines)


def _zip(payload: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a valid OOXML package") from exc


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        data = archive.read(name)
    except KeyError as exc:
        raise ValueError(f"missing required OOXML part: {name}") from exc
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"malformed XML part: {name}") from exc


def _read_docx(payload: bytes) -> str:
    with _zip(payload) as archive:
        root = _xml(archive, "word/document.xml")
    body = root.find(f"{{{_W_NS}}}body")
    blocks: list[str] = []
    if body is not None:
        for child in body:
            if child.tag == f"{{{_W_NS}}}p":
                line = _docx_paragraph(child)
                if line.strip():
                    blocks.append(_docx_heading(child, line))
            elif child.tag == f"{{{_W_NS}}}tbl":
                table = _docx_table(child)
                if table:
                    blocks.append(_markdown_table(table))
    return _finalize("\n\n".join(blocks))


def _docx_paragraph(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{{{_W_NS}}}t":
            parts.append(node.text or "")
        elif node.tag == f"{{{_W_NS}}}tab":
            parts.append("\t")
        elif node.tag in (f"{{{_W_NS}}}br", f"{{{_W_NS}}}cr"):
            parts.append("\n")
    return "".join(parts)


def _docx_heading(paragraph: ET.Element, line: str) -> str:
    style = paragraph.find(f"{{{_W_NS}}}pPr/{{{_W_NS}}}pStyle")
    value = style.get(f"{{{_W_NS}}}val") if style is not None else ""
    match = re.fullmatch(r"(?:Heading)?([1-6])", value or "")
    if match:
        level = int(match.group(1))
        return "#" * level + " " + line.lstrip()
    return line


def _docx_table(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall(f"{{{_W_NS}}}tr"):
        cells: list[str] = []
        for cell in row.findall(f"{{{_W_NS}}}tc"):
            pieces = [
                _docx_paragraph(paragraph).strip()
                for paragraph in cell.findall(f"{{{_W_NS}}}p")
            ]
            cells.append(" ".join(piece for piece in pieces if piece))
        if any(cell.strip() for cell in cells):
            rows.append(cells)
    return rows


def _read_pptx(payload: bytes) -> str:
    with _zip(payload) as archive:
        names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda name: int(re.search(r"(\d+)\.xml$", name).group(1)),
        )
        if not names:
            raise ValueError("pptx contains no slides")
        blocks: list[str] = []
        for index, name in enumerate(names, start=1):
            root = _xml(archive, name)
            lines = []
            for paragraph in root.iter(f"{{{_A_NS}}}p"):
                text = "".join(node.text or "" for node in paragraph.iter(f"{{{_A_NS}}}t"))
                if text.strip():
                    lines.append(text.strip())
            if lines:
                blocks.append(f"## 幻灯片 {index}\n\n" + "\n".join(lines))
    return _finalize("\n\n".join(blocks))


def _read_xlsx(payload: bytes) -> str:
    with _zip(payload) as archive:
        shared = _xlsx_shared_strings(archive)
        rels = _xlsx_workbook_rels(archive)
        workbook = _xml(archive, "xl/workbook.xml")
        sheets = workbook.find(f"{{{_MAIN_NS}}}sheets")
        if sheets is None:
            raise ValueError("xlsx workbook declares no sheets")
        blocks: list[str] = []
        for sheet in sheets:
            target = rels.get(sheet.get(f"{{{_DOC_REL_NS}}}id"))
            if not target:
                continue
            path = _xlsx_sheet_path(target)
            rows = _xlsx_sheet_rows(_xml(archive, path), shared)
            if not rows or not any(any(cell.strip() for cell in row) for row in rows):
                continue
            name = sheet.get("name") or path
            blocks.append(f"## 工作表：{name}\n\n" + _markdown_table(rows))
    if not blocks:
        raise ValueError("xlsx contains no readable cell content")
    return _finalize("\n\n".join(blocks))


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _xml(archive, "xl/sharedStrings.xml")
    values: list[str] = []
    for item in root.findall(f"{{{_MAIN_NS}}}si"):
        values.append("".join(node.text or "" for node in item.iter(f"{{{_MAIN_NS}}}t")))
    return values


def _xlsx_workbook_rels(archive: zipfile.ZipFile) -> dict[str, str]:
    root = _xml(archive, "xl/_rels/workbook.xml.rels")
    rels: dict[str, str] = {}
    for relationship in root:
        rels[relationship.get("Id")] = relationship.get("Target") or ""
    return rels


def _xlsx_sheet_path(target: str) -> str:
    target = target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return f"xl/{target}"


def _column_index(letters: str) -> int:
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _xlsx_sheet_rows(root: ET.Element, shared: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in root.iter(f"{{{_MAIN_NS}}}row"):
        cells: dict[int, str] = {}
        for cell in row.findall(f"{{{_MAIN_NS}}}c"):
            reference = cell.get("r") or ""
            letters = re.match(r"[A-Za-z]+", reference)
            position = _column_index(letters.group(0)) if letters else len(cells)
            cells[position] = _xlsx_cell_value(cell, shared)
        if not cells:
            rows.append([])
            continue
        width = max(cells) + 1
        rows.append([cells.get(index, "") for index in range(width)])
    return rows


def _xlsx_cell_value(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find(f"{{{_MAIN_NS}}}is")
        if inline is None:
            return ""
        return "".join(node.text or "" for node in inline.iter(f"{{{_MAIN_NS}}}t"))
    value = cell.find(f"{{{_MAIN_NS}}}v")
    if value is None or value.text is None:
        return ""
    if kind == "s":
        try:
            index = int(value.text)
        except ValueError:
            return ""
        return shared[index] if 0 <= index < len(shared) else ""
    return value.text


class _HTMLTextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template"})
    _LINE_BREAKS = frozenset({
        "p", "div", "section", "article", "header", "footer", "nav", "aside", "main",
        "li", "ul", "ol", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr", "br",
        "figure", "figcaption", "address", "fieldset", "form", "title",
    })
    _HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6, "title": 1}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._lines: list[str] = []
        self._buffer: list[str] = []
        self._skip_depth = 0
        self._heading_level = 0

    def _flush(self) -> None:
        line = "".join(self._buffer)
        self._buffer = []
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if not line:
            return
        if self._heading_level:
            line = "#" * min(self._heading_level, 6) + " " + line
            self._heading_level = 0
        self._lines.append(line)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._LINE_BREAKS:
            self._flush()
        if tag in self._HEADINGS and not self._buffer:
            self._heading_level = self._HEADINGS[tag]

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in self._LINE_BREAKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        self._buffer.append(data)

    def result(self) -> str:
        self._flush()
        return "\n".join(self._lines)


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("latin-1")


def _read_html(payload: bytes) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(_decode_text(payload))
    extractor.close()
    return _finalize(extractor.result())


def _read_json(payload: bytes) -> str:
    data = json.loads(payload.decode("utf-8-sig"))
    return _finalize(json.dumps(data, ensure_ascii=False, indent=2))


def _read_pdf(payload: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DependencyMissing("PDF intake requires the optional dependency pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(payload))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise ValueError(f"pdf could not be read deterministically: {exc}") from exc
    blocks = [f"## 第 {index} 页\n\n{text}" for index, text in enumerate(pages, start=1) if text]
    return _finalize("\n\n".join(blocks))
