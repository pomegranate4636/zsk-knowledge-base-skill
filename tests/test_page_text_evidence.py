from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import PageArtifact, PageTextEvidence  # noqa: E402
from shared import ocr_provider  # noqa: E402
from shared.ocr_provider import AutoOcrProvider, OcrFailed, OcrResult, OcrUnavailable, TesseractOcrProvider  # noqa: E402
from shared.page_text import PptxPageText, build_page_text_evidence, extract_pptx_page_text  # noqa: E402
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


def pptx_chart_payload() -> bytes:
    presentation = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
      xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
      <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
    </p:presentation>"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/>
    </Relationships>"""
    chart_slide = """<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
      <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>季度收入</a:t></a:r></a:p></p:txBody></p:sp>
      <p:graphicFrame><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"/></a:graphic></p:graphicFrame>
      </p:spTree></p:cSld>
    </p:sld>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", relationships)
        archive.writestr("ppt/slides/slide1.xml", chart_slide)
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
    def test_tesseract_provider_parses_local_tsv_confidence(self) -> None:
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
        output = header + "\n5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\tHello\n5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t80\t世界\n"
        completed = subprocess.CompletedProcess(("tesseract",), 0, output, "")
        with mock.patch("shared.ocr_provider.subprocess.run", return_value=completed):
            result = TesseractOcrProvider("tesseract.exe").recognize(PNG_1)
        self.assertEqual(result.text, "Hello 世界")
        self.assertAlmostEqual(result.confidence, 0.871428, places=5)

    def test_tesseract_provider_fails_closed(self) -> None:
        completed = subprocess.CompletedProcess(("tesseract",), 1, "", "failed")
        with mock.patch("shared.ocr_provider.subprocess.run", return_value=completed):
            with self.assertRaises(OcrFailed):
                TesseractOcrProvider("tesseract.exe").recognize(PNG_1)
        with mock.patch("shared.ocr_provider._tesseract_executable", return_value=None):
            with self.assertRaises(OcrUnavailable):
                TesseractOcrProvider()
        with self.assertRaises(OcrFailed):
            TesseractOcrProvider("tesseract.exe").recognize(b"")
        with mock.patch(
            "shared.ocr_provider.subprocess.run",
            side_effect=subprocess.TimeoutExpired(("tesseract.exe",), 120),
        ):
            with self.assertRaises(OcrFailed):
                TesseractOcrProvider("tesseract.exe").recognize(PNG_1)

    def test_provider_finds_standard_windows_install_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "Tesseract-OCR" / "tesseract.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"exe")
            with mock.patch.dict(os.environ, {"ProgramFiles": folder}, clear=False):
                with mock.patch.object(ocr_provider.platform, "system", return_value="Windows"):
                    with mock.patch.object(ocr_provider.shutil, "which", return_value=None):
                        self.assertEqual(ocr_provider._tesseract_executable(), str(executable))

    def test_provider_uses_user_level_tessdata_directory(self) -> None:
        tessdata = Path("C:/fake/tessdata")
        completed = subprocess.CompletedProcess(("tesseract",), 0, "", "")
        with mock.patch("shared.ocr_provider._tessdata_directory", return_value=tessdata):
            with mock.patch("shared.ocr_provider.subprocess.run", return_value=completed) as run:
                TesseractOcrProvider("tesseract.exe").recognize(PNG_1)
        command = run.call_args.args[0]
        self.assertIn("--tessdata-dir", command)
        self.assertIn(str(tessdata), command)

    def test_extracts_native_text_and_detects_image_only_page(self) -> None:
        pages = extract_pptx_page_text(pptx_payload())
        self.assertEqual([item.page_number for item in pages], [1, 2])
        self.assertEqual(pages[0].native_text, "原生标题\n原生正文")
        self.assertFalse(pages[0].requires_ocr)
        self.assertTrue(pages[1].requires_ocr)

    def test_chart_page_requires_ocr_even_when_title_is_native(self) -> None:
        chart = extract_pptx_page_text(pptx_chart_payload())[0]
        self.assertEqual(chart.native_text, "季度收入")
        self.assertTrue(chart.requires_ocr)

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

    def test_pdf_uses_meaningful_embedded_text_before_ocr(self) -> None:
        provider = FakeOcr(0.96)
        with mock.patch(
            "shared.page_text.extract_pdf_page_inputs",
            return_value=(
                PptxPageText(1, "翠湖天地一期项目资料与区位介绍及建筑品质配套说明详细资料", False, False),
                PptxPageText(2, "建筑面积二百平方米房屋格局配套说明以及产权和交易条件资料", False, False),
            ),
        ):
            evidence = build_page_text_evidence(
                SOURCE_ID,
                ".pdf",
                b"pdf",
                (page(1, PNG_1), page(2, PNG_2)),
                provider,
            )
        self.assertEqual(provider.calls, [])
        self.assertEqual([item.review_status for item in evidence], ["verified_native", "verified_native"])
        self.assertEqual(
            [item.verbatim_text for item in evidence],
            ["翠湖天地一期项目资料与区位介绍及建筑品质配套说明详细资料", "建筑面积二百平方米房屋格局配套说明以及产权和交易条件资料"],
        )

    def test_pdf_template_placeholder_is_not_trusted_as_native_text(self) -> None:
        provider = FakeOcr(0.96)
        with mock.patch(
            "shared.page_text.extract_pdf_page_inputs",
            return_value=(
                PptxPageText(1, "", False, True),
                PptxPageText(2, "", False, True),
            ),
        ):
            evidence = build_page_text_evidence(
                SOURCE_ID,
                ".pdf",
                b"pdf",
                (page(1, PNG_1), page(2, PNG_2)),
                provider,
            )
        self.assertEqual(provider.calls, [PNG_1, PNG_2])
        self.assertEqual([item.text_source for item in evidence], ["ocr", "ocr"])

    def test_pdf_page_with_native_text_and_visuals_still_uses_ocr(self) -> None:
        provider = FakeOcr(0.96)
        with mock.patch(
            "shared.page_text.extract_pdf_page_inputs",
            return_value=(PptxPageText(1, "页面原生文字已经足够长但图表仍包含关键数据", True, True),),
        ):
            evidence = build_page_text_evidence(
                SOURCE_ID,
                ".pdf",
                b"pdf",
                (page(1, PNG_1),),
                provider,
            )
        self.assertEqual(provider.calls, [PNG_1])
        self.assertEqual(evidence[0].text_source, "native+ocr")

    def test_low_confidence_ocr_is_marked_unverified_without_requesting_human_review(self) -> None:
        evidence = build_page_text_evidence(
            SOURCE_ID,
            ".pptx",
            pptx_payload(),
            (page(1, PNG_1), page(2, PNG_2)),
            FakeOcr(0.42),
        )[1]
        self.assertEqual(evidence.review_status, "review_required")
        self.assertEqual(evidence.verbatim_text, "")

    def test_automatic_ocr_accepts_agreeing_local_passes(self) -> None:
        provider = AutoOcrProvider((FakeOcr(0.96), FakeOcr(0.91)))
        result = provider.recognize(PNG_1)
        self.assertEqual(result.text, "图片中的文字")
        self.assertEqual(result.confidence, 0.91)

    def test_automatic_ocr_rejects_disagreeing_local_passes(self) -> None:
        class DifferentOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrResult:
                self.calls.append(image)
                return OcrResult("另一段文字", self.confidence, self.name)

        result = AutoOcrProvider((FakeOcr(0.99), DifferentOcr(0.99))).recognize(PNG_1)
        self.assertEqual(result.confidence, 0.0)

    def test_automatic_ocr_rejects_partial_local_corroboration(self) -> None:
        class CorroboratingOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrResult:
                self.calls.append(image)
                return OcrResult("上海 翠湖 天地 高端 住宅 项目 资料", self.confidence, self.name)

        class PrimaryOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrResult:
                self.calls.append(image)
                return OcrResult("上海翠湖天地高端住宅", self.confidence, self.name)

        result = AutoOcrProvider((PrimaryOcr(0.91), CorroboratingOcr(0.72))).recognize(PNG_1)
        self.assertEqual(result.confidence, 0.0)

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
