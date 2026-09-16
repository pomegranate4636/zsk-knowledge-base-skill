"""Local format probes using synthetic, non-customer documents; no downloads."""
from __future__ import annotations

from io import BytesIO
import zipfile
from .markdown_converter import convert_to_markdown, ConverterUnavailable, ConversionFailed

FORMATS = ("docx", "pdf", "pptx", "xlsx", "html", "json")
MARKER = "ZSKProbe2026"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _zip(parts: dict[str, str], types: dict[str, str]) -> bytes:
    parts = dict(parts)
    parts["[Content_Types].xml"] = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + ''.join(f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, kind in types.items()) + '</Types>'
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return output.getvalue()


def _rels(kind: str, target: str) -> str:
    return f'<Relationships xmlns="{_REL}"><Relationship Id="rId1" Type="{_OFFICE}/{kind}" Target="{target}"/></Relationships>'


def sample_document(kind: str) -> bytes:
    if kind == "html":
        return f'<html><body><p>{MARKER}</p></body></html>'.encode()
    if kind == "json":
        return ('{"probe":"' + MARKER + '"}').encode()
    if kind == "docx":
        return _zip({
            '_rels/.rels': _rels('officeDocument', 'word/document.xml'),
            'word/document.xml': f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{MARKER}</w:t></w:r></w:p></w:body></w:document>',
        }, {'word/document.xml': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'})
    if kind == "xlsx":
        ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        return _zip({
            '_rels/.rels': _rels('officeDocument', 'xl/workbook.xml'),
            'xl/workbook.xml': f'<workbook xmlns="{ns}" xmlns:r="{_OFFICE}"><sheets><sheet name="Probe" sheetId="1" r:id="rId1"/></sheets></workbook>',
            'xl/_rels/workbook.xml.rels': _rels('worksheet', 'worksheets/sheet1.xml'),
            'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{ns}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{MARKER}</t></is></c></row></sheetData></worksheet>',
        }, {'xl/workbook.xml': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml', 'xl/worksheets/sheet1.xml': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'})
    if kind == "pptx":
        ns = 'http://schemas.openxmlformats.org/presentationml/2006/main'
        drawing = 'http://schemas.openxmlformats.org/drawingml/2006/main'
        return _zip({
            '_rels/.rels': _rels('officeDocument', 'ppt/presentation.xml'),
            'ppt/presentation.xml': f'<p:presentation xmlns:p="{ns}" xmlns:r="{_OFFICE}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
            'ppt/_rels/presentation.xml.rels': _rels('slide', 'slides/slide1.xml'),
            'ppt/slides/slide1.xml': f'<p:sld xmlns:p="{ns}" xmlns:a="{drawing}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Probe"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{MARKER}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
        }, {'ppt/presentation.xml': 'application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml', 'ppt/slides/slide1.xml': 'application/vnd.openxmlformats-officedocument.presentationml.slide+xml'})
    if kind == "pdf":
        stream = f'BT /F1 12 Tf 20 100 Td ({MARKER}) Tj ET'.encode()
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
        result = bytearray(b'%PDF-1.4\n'); offsets = [0]
        for i, body in enumerate(objects, 1):
            offsets.append(len(result)); result.extend(f'{i} 0 obj\n'.encode() + body + b'\nendobj\n')
        start = len(result)
        result.extend(b'xref\n0 6\n0000000000 65535 f \n')
        for offset in offsets[1:]:
            result.extend(f'{offset:010d} 00000 n \n'.encode())
        result.extend(f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
        return bytes(result)
    raise ValueError(f'Unsupported probe format: {kind}')


def probe_formats(formats: tuple[str, ...]) -> dict[str, bool]:
    results = {}
    for kind in formats:
        payload = sample_document(kind)
        try:
            result = convert_to_markdown(payload, '.' + kind)
            results[kind] = MARKER in result.text
        except (ConverterUnavailable, ConversionFailed):
            results[kind] = False
    return results
