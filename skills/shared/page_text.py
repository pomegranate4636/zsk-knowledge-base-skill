"""PPT native text, image-page detection, OCR review, and page text hashing."""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import PurePosixPath
import re
from typing import Mapping
import xml.etree.ElementTree as ET
import zipfile

from .contracts import PageTextEvidence
from .ocr_provider import LocalOcrProvider, OcrFailed, OcrUnavailable, default_auto_ocr_provider
from .page_renderer import RenderedPage


class PageTextFailed(ValueError):
    pass


@dataclass(frozen=True)
class PptxPageText:
    page_number: int
    native_text: str
    has_image: bool
    requires_ocr: bool


_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_PDF_TEMPLATE_PLACEHOLDER = re.compile(r"(?:空白演示|单击输入您的(?:封面)?副标题|单击输入您的标题)")


def _clean_text(parts: list[str]) -> str:
    return "\n".join(value for value in (re.sub(r"\s+", " ", part).strip() for part in parts) if value)


def extract_pptx_page_text(payload: bytes) -> tuple[PptxPageText, ...]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
            relationships = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
            targets = {
                item.attrib.get("Id", ""): item.attrib.get("Target", "")
                for item in relationships.findall(f"{{{_PKG_REL}}}Relationship")
            }
            slide_paths: list[str] = []
            for slide_id in presentation.findall(f".//{{{_P}}}sldId"):
                relation_id = slide_id.attrib.get(f"{{{_R}}}id", "")
                target = targets.get(relation_id, "")
                if not target:
                    raise PageTextFailed("PPT slide relationship is incomplete")
                slide_paths.append(str(PurePosixPath("ppt") / target))
            pages: list[PptxPageText] = []
            for number, path in enumerate(slide_paths, start=1):
                root = ET.fromstring(archive.read(path))
                native = _clean_text([item.text or "" for item in root.findall(f".//{{{_A}}}t")])
                graphic_uris = (
                    str(item.attrib.get("uri", "")).lower()
                    for item in root.findall(f".//{{{_A}}}graphicData")
                )
                has_visual = root.find(f".//{{{_P}}}pic") is not None or any(
                    marker in uri
                    for uri in graphic_uris
                    for marker in ("chart", "diagram", "ole")
                )
                pages.append(PptxPageText(number, native, has_visual, has_visual or not bool(native)))
    except (KeyError, ET.ParseError, zipfile.BadZipFile, OSError) as exc:
        raise PageTextFailed("PPT native text cannot be extracted safely") from exc
    if not pages:
        raise PageTextFailed("PPT contains no slides")
    return tuple(pages)


def extract_pdf_page_inputs(payload: bytes, expected_page_count: int) -> tuple[PptxPageText, ...]:
    """读取 PDF 原生文字，并标记仍需从完整页图核验的视觉页。"""
    if expected_page_count < 1:
        raise PageTextFailed("PDF has no rendered pages")
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(payload)) as document:
            pages = tuple(document.pages)
            if len(pages) != expected_page_count:
                raise PageTextFailed("PDF native page count differs from rendered pages")
            results: list[PptxPageText] = []
            for number, page in enumerate(pages, start=1):
                native = _usable_pdf_native_text(page.extract_text() or "")
                has_visual = bool(page.images or page.curves or page.rects)
                results.append(PptxPageText(number, native, has_visual, has_visual or not bool(native)))
    except Exception:
        return tuple(PptxPageText(number, "", True, True) for number in range(1, expected_page_count + 1))
    return tuple(results)


def extract_pdf_page_text(payload: bytes, expected_page_count: int) -> tuple[str, ...]:
    """兼容只读取 PDF 原生文字的调用方。"""
    return tuple(item.native_text for item in extract_pdf_page_inputs(payload, expected_page_count))


def _usable_pdf_native_text(text: str) -> str:
    normalized = _clean_text(text.splitlines())
    if len(normalized) < 24 or _PDF_TEMPLATE_PLACEHOLDER.search(normalized):
        return ""
    return normalized


def _merge_text(native: str, ocr: str) -> str:
    if not native:
        return ocr
    if not ocr or ocr in native:
        return native
    if native in ocr:
        return ocr
    return native + "\n" + ocr


def build_page_text_evidence(
    source_id: str,
    suffix: str,
    source_payload: bytes,
    rendered_pages: tuple[RenderedPage, ...],
    provider: LocalOcrProvider | None,
    *,
    corrections: Mapping[int, str] | None = None,
    confidence_threshold: float = 0.85,
) -> tuple[PageTextEvidence, ...]:
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("OCR confidence threshold is invalid")
    corrections = dict(corrections or {})
    if suffix == ".pptx":
        page_inputs = extract_pptx_page_text(source_payload)
    elif suffix == ".pdf":
        page_inputs = extract_pdf_page_inputs(source_payload, len(rendered_pages))
    else:
        raise PageTextFailed("page text evidence only supports PDF and PPTX")
    if [item.page_number for item in page_inputs] != [item.artifact.page_number for item in rendered_pages]:
        raise PageTextFailed("page text and rendered page counts differ")
    unknown_corrections = set(corrections) - {item.page_number for item in page_inputs}
    if unknown_corrections:
        raise PageTextFailed("OCR correction refers to an unknown page")

    evidence: list[PageTextEvidence] = []
    active_provider = provider
    for page_input, rendered in zip(page_inputs, rendered_pages, strict=True):
        native = page_input.native_text
        ocr_text = ""
        confidence = 1.0
        source = "native" if native else "none"
        status = "verified_native"
        verbatim = native
        if page_input.requires_ocr:
            if active_provider is None:
                active_provider = default_auto_ocr_provider()
            try:
                result = active_provider.recognize(rendered.payload)
            except OcrUnavailable:
                raise
            except OcrFailed:
                raise
            ocr_text = result.text.strip()
            confidence = result.confidence
            source = "native+ocr" if native else "ocr"
            correction = corrections.get(page_input.page_number, "").strip()
            if correction:
                verbatim = correction
                status = "approved"
            elif confidence >= confidence_threshold and ocr_text:
                verbatim = _merge_text(native, ocr_text)
                status = "auto_verified"
            else:
                verbatim = ""
                status = "review_required"
        evidence.append(
            PageTextEvidence.create(
                source_id,
                page_input.page_number,
                rendered.artifact.sha256,
                native_text=native,
                ocr_text=ocr_text,
                verbatim_text=verbatim,
                text_source=source,
                confidence=confidence,
                review_status=status,
            )
        )
    return tuple(evidence)
