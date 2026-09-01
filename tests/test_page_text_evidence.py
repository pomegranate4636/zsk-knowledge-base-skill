from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import PageArtifact, PageTextEvidence  # noqa: E402
from shared.ocr_provider import OcrResult  # noqa: E402
from shared.page_text import OcrReviewRequired, build_page_text_evidence, extract_pptx_page_text  # noqa: E402
from shared.page_renderer import RenderedPage  # noqa: E402


PNG_1 = b"\x89PNG\r\n\x1a\n" + b"page-one"
PNG_2 = b"\x89PNG\r\n\x1a\n" + b"page-two"
SOURCE_ID = "SRC-" + hashlib.sha256(b"deck").hexdigest()[:24]


def page(number: int, payload: bytes) -> RenderedPage:
    return RenderedPage(
        PageArtifact(
            f"{SOURCE_ID}-PAGE-{number:03d}",
            SOURCE_ID,
            number,
            f"page-{number:03d}.png",
            hashlib.sha256(payload).hexdigest(),
            1600,
            900,
        ),
        payload,
    )


def pptx_payload() -> bytes:
    presentation = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
      xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
      <p:sldIdLst><p:sldId id="256" r:id="rId1"/><p:sldId id="257" r:id="rId2"/></p:sldIdLst>
    </p:presentation>"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/>
      <Relationship Id="rId2" Type="slide" Target="slides/slide2.xml"/>
    </Relationships>"""
    native_slide = """<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
      <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>原生标题</a:t></a:r></a:p>
      <a:p><a:r><a:t>原生正文</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
    </p:sld>"""
    image_slide = """<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
      <p:cSld><p:spTree><p:pic><p:nvPicPr/><p:blipFill/></p:pic></p:spTree></p:cSld>
    </p:sld>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", relationships)
        archive.writestr("ppt/slides/slide1.xml", native_slide)
        archive.writestr("ppt/slides/slide2.xml", image_slide)
    return buffer.getvalue()


class FakeOcr:
    name = "fake-local-ocr"

    def __init__(self, confidence: float) -> None:
        self.confidence = confidence
        self.calls: list[bytes] = []

    def recognize(self, image: bytes) -> OcrResult:
        self.calls.append(image)
        return OcrResult("图片中的文字", self.confidence, self.name)


class PageTextEvidenceTests(unittest.TestCase):
    def test_extracts_native_text_and_detects_image_only_page(self) -> None:
        pages = extract_pptx_page_text(pptx_payload())
        self.assertEqual([item.page_number for item in pages], [1, 2])
        self.assertEqual(pages[0].native_text, "原生标题\n原生正文")
        self.assertFalse(pages[0].requires_ocr)
        self.assertTrue(pages[1].requires_ocr)

    def test_only_image_page_uses_local_ocr(self) -> None:
        provider = FakeOcr(0.96)
        evidence = build_page_text_evidence(
            SOURCE_ID,
            ".pptx",
            pptx_payload(),
            (page(1, PNG_1), page(2, PNG_2)),
            provider,
        )
        self.assertEqual(provider.calls, [PNG_2])
        self.assertEqual(evidence[0].text_source, "native")
        self.assertEqual(evidence[1].text_source, "ocr")
        self.assertEqual(evidence[1].review_status, "auto_verified")

    def test_low_confidence_ocr_stops_without_review(self) -> None:
        with self.assertRaises(OcrReviewRequired) as raised:
            build_page_text_evidence(
                SOURCE_ID,
                ".pptx",
                pptx_payload(),
                (page(1, PNG_1), page(2, PNG_2)),
                FakeOcr(0.42),
            )
        self.assertEqual(raised.exception.page_numbers, (2,))

    def test_reviewed_correction_is_hashed_with_page_image(self) -> None:
        evidence = build_page_text_evidence(
            SOURCE_ID,
            ".pptx",
            pptx_payload(),
            (page(1, PNG_1), page(2, PNG_2)),
            FakeOcr(0.42),
            corrections={2: "人工校对后的文字"},
        )[1]
        self.assertEqual(evidence.review_status, "approved")
        self.assertEqual(evidence.verbatim_text, "人工校对后的文字")
        material = {
            "source_id": SOURCE_ID,
            "page_number": 2,
            "page_sha256": hashlib.sha256(PNG_2).hexdigest(),
            "native_text": "",
            "ocr_text": "图片中的文字",
            "verbatim_text": "人工校对后的文字",
            "text_source": "ocr",
            "confidence": 0.42,
            "review_status": "approved",
        }
        expected = hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(evidence.evidence_sha256, expected)
        self.assertIsInstance(evidence, PageTextEvidence)


if __name__ == "__main__":
    unittest.main()
