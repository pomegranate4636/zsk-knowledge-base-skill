from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import install as installer


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, Binding, MediaArtifact  # noqa: E402
from shared.fake_adapter import FakeAdapter  # noqa: E402
from shared.feishu_cli import RecordedCliCall, RecordedCliRunner  # noqa: E402
from shared.feishu_stage5 import FeishuStage5Storage  # noqa: E402
from shared.markdown_converter import MarkdownConversion  # noqa: E402
from shared import media_renderer  # noqa: E402
from shared.media_renderer import RenderedMedia  # noqa: E402
from shared.stage5_intake import IntakeRequest, Stage5Intake  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"


def binding() -> Binding:
    return Binding(
        BINDING_SCHEMA, "CLT-1234567890ABCD", "验收客户", "验收知识库", "company", "obsidian",
        str(ROOT), {key: f"root:{key}" for key in ROOT_KEYS}, TEMPLATE_VERSION,
    )


class MediaRendererTests(unittest.TestCase):
    def test_renderer_has_numeric_page_order_contract(self) -> None:
        page_number = getattr(media_renderer, "_page_number", None)
        self.assertTrue(callable(page_number))
        names = ["page-10.png", "page-2.png", "page-1.png"]
        self.assertEqual(sorted(names, key=lambda name: page_number(Path(name))), ["page-1.png", "page-2.png", "page-10.png"])

    def test_rendered_media_carries_page_ocr_markdown(self) -> None:
        rendered = RenderedMedia((), 0, "test")
        self.assertTrue(hasattr(rendered, "ocr_markdown"))
        self.assertEqual(rendered.ocr_markdown, "")

    @mock.patch("pathlib.Path.write_text")
    @mock.patch("pathlib.Path.is_file", return_value=True)
    @mock.patch("shared.media_renderer.shutil.which", return_value="C:/Windows/powershell.exe")
    @mock.patch("shared.media_renderer.subprocess.run", side_effect=subprocess.TimeoutExpired(("powershell",), 180))
    def test_powerpoint_timeout_becomes_safe_media_failure(self, _run, _which, _is_file, _write_text) -> None:
        with self.assertRaises(media_renderer.MediaRenderFailed):
            media_renderer._render_pptx_to_pdf(Path("source.pptx"), Path("source.pdf"), Path("work"))

    def test_relative_media_output_is_resolved_before_powerpoint(self) -> None:
        output = ROOT / ".relative-media-test"
        work = output / "work"

        class FixedTemporaryDirectory:
            def __enter__(self):
                work.mkdir(parents=True, exist_ok=True)
                return str(work)

            def __exit__(self, *_args):
                return False

        def convert(source, pdf, work_dir):
            self.assertTrue(source.is_absolute())
            self.assertTrue(pdf.is_absolute())
            self.assertTrue(work_dir.is_absolute())
            pdf.write_bytes(b"pdf")
            return "test-powerpoint"

        def render(argv, **_kwargs):
            Path(str(argv[-1]) + "-1.png").write_bytes(b"png")
            return subprocess.CompletedProcess(argv, 0, "", "")

        try:
            with mock.patch("shared.media_renderer._render_pptx_to_pdf", side_effect=convert), \
                 mock.patch("shared.media_renderer._run_process", side_effect=render), \
                 mock.patch("shared.media_renderer.shutil.which", return_value="pdftoppm"), \
                 mock.patch("shared.media_renderer._ocr_page", return_value=("OCR", "test-ocr")), \
                 mock.patch("shared.media_renderer.tempfile.TemporaryDirectory", return_value=FixedTemporaryDirectory()):
                result = media_renderer.render_pages(b"pptx", ".pptx", "SRC-" + "d" * 24, Path(".relative-media-test"))
            self.assertEqual(result.page_count, 1)
        finally:
            page = output / "page-001.png"
            if page.exists():
                page.unlink()
            for name in ("source.pptx", "source.pdf", "page-1.png"):
                path = work / name
                if path.exists():
                    path.unlink()
            if work.exists():
                work.rmdir()
            if output.exists():
                output.rmdir()

    @mock.patch("install.os.name", "nt")
    @mock.patch("install.Path.is_file", return_value=True)
    @mock.patch("install.shutil.which")
    def test_doctor_requires_pdf_ppt_and_ocr_renderers(self, which, _is_file) -> None:
        which.side_effect = lambda name: {
            "pdftoppm": "C:/runtime/pdftoppm.exe",
            "powershell.exe": "C:/Windows/powershell.exe",
        }.get(name)
        status = installer.rich_media_status()
        self.assertEqual(status, {"pdf_pages": True, "ppt_pages": True, "ocr": True})


class MediaIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = ROOT / ".media-test"
        self.binding = binding()
        self.adapter = FakeAdapter()
        self.adapter.resolve_binding(self.binding)
        self.adapter.create_skeleton(self.binding)

    @mock.patch("pathlib.Path.read_bytes", return_value=b"image")
    @mock.patch("shared.stage5_intake.render_pages")
    @mock.patch("shared.stage5_intake.convert_to_markdown")
    def test_pdf_registers_page_images_as_source_evidence(self, convert, render, _read_bytes) -> None:
        convert.return_value = MarkdownConversion("# PDF\n", "markitdown", "0.1.6")
        digest = hashlib.sha256(b"image").hexdigest()
        artifact = MediaArtifact("SRC-" + hashlib.sha256(b"pdf").hexdigest()[:24] + "-PAGE-001", "SRC-" + hashlib.sha256(b"pdf").hexdigest()[:24], 1, "image", "page-001.png", digest, ocr_text_sha256=hashlib.sha256(b"OCR").hexdigest())
        render.return_value = RenderedMedia((artifact,), 1, "pdftoppm+tesseract", "## 第 1 页\n\nOCR\n")

        response = Stage5Intake(self.adapter).execute(
            IntakeRequest(TASK_ID, self.binding, "资料.pdf", b"pdf", "资料", "business_knowledge", media_output_dir=str(self.output))
        )

        self.assertEqual((response.status, response.code), ("registered", None))
        self.assertEqual(response.record.content_kind, "document")
        self.assertEqual(response.record.page_count, 1)
        self.assertEqual(response.record.visual_processing_status, "ocr_completed")
        self.assertEqual(len(response.record.media_artifacts), 1)
        self.assertTrue(any(event["action"] == "store_media" for event in response.evidence["events"]))

    def test_feishu_media_upload_is_content_addressed_by_page(self) -> None:
        payload = b"image"
        digest = hashlib.sha256(payload).hexdigest()
        source_id = "SRC-" + hashlib.sha256(b"pdf").hexdigest()[:24]
        artifact = MediaArtifact(f"{source_id}-PAGE-001", source_id, 1, "image", "page-001.png", digest)
        from shared.contracts import SOURCE_SCHEMA, SourceRecord
        source = SourceRecord(
            SOURCE_SCHEMA, source_id, self.binding.client_id, "资料", "business_knowledge", "document", "资料.pdf",
            hashlib.sha256(b"pdf").hexdigest(), hashlib.sha256(b"readable").hexdigest(), "passed", "allowed", None,
            "registered", True, media_artifacts=(artifact,), page_count=1, visual_processing_status="rendered",
        )
        list_call = ("lark-cli", "--as", "user", "wiki", "nodes", "list", "--space-id", "1", "--parent-node-token", "root-01", "--page-all", "--format", "json")
        upload_call = ("lark-cli", "--as", "user", "drive", "+upload", "--file", "{file}", "--wiki-token", "root-01", "--name", f"{source_id}-page-001.png", "--format", "json")
        runner = RecordedCliRunner((
            RecordedCliCall(list_call, '{"ok":true,"data":{"items":[]}}'),
            RecordedCliCall(upload_call, '{"ok":true,"data":{"file_token":"token"}}', payload=payload, upload_name=f"{source_id}-page-001.png"),
        ))

        result = FeishuStage5Storage(runner, "1", {"01": "root-01", "02": "root-02"}).store_media(source, artifact, payload)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.object_refs[0].object_kind, "source_media")
        self.assertTrue(runner.exhausted)


if __name__ == "__main__":
    unittest.main()
