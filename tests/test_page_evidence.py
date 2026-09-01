from __future__ import annotations

import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, SOURCE_SCHEMA, Binding, PageArtifact, SourceRecord  # noqa: E402
from shared.fake_adapter import FakeAdapter  # noqa: E402
from shared.feishu_cli import RecordedCliCall, RecordedCliRunner  # noqa: E402
from shared.feishu_stage5 import FeishuStage5Storage  # noqa: E402
from shared.markdown_converter import MarkdownConversion  # noqa: E402
from shared.obsidian_adapter import ObsidianAdapter  # noqa: E402
from shared import page_renderer  # noqa: E402
from shared.page_renderer import PageRenderFailed, PageRendererUnavailable, RenderedPage, RenderedPages  # noqa: E402
from shared.stage5_intake import IntakeRequest, Stage5Intake  # noqa: E402
from shared.stage6_knowledge import KnowledgeRequest, Stage6Knowledge  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"
PNG = b"\x89PNG\r\n\x1a\nsynthetic-page"


def binding(backend: str = "obsidian", locator: str | None = None) -> Binding:
    return Binding(
        BINDING_SCHEMA,
        "CLT-1234567890ABCD",
        "验收客户",
        "验收知识库",
        "company",
        backend,
        locator or str(ROOT),
        {key: f"root:{key}" for key in ROOT_KEYS},
        TEMPLATE_VERSION,
    )


def rendered(payload: bytes = PNG) -> RenderedPages:
    source_id = "SRC-" + hashlib.sha256(b"pdf").hexdigest()[:24]
    artifact = PageArtifact(
        f"{source_id}-PAGE-001",
        source_id,
        1,
        "page-001.png",
        hashlib.sha256(payload).hexdigest(),
    )
    return RenderedPages((RenderedPage(artifact, payload),), 1, "pdftoppm")


class PageEvidenceIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = binding()
        self.adapter = FakeAdapter()
        self.adapter.resolve_binding(self.binding)
        self.adapter.create_skeleton(self.binding)

    @mock.patch("shared.stage5_intake.render_page_evidence")
    @mock.patch("shared.stage5_intake.convert_to_markdown")
    def test_required_pages_are_registered_with_persistent_manifest(self, convert, render) -> None:
        convert.return_value = MarkdownConversion("# PDF\n", "markitdown", "0.1.6")
        render.return_value = rendered()
        response = Stage5Intake(self.adapter).execute(
            IntakeRequest(
                TASK_ID,
                self.binding,
                "资料.pdf",
                b"pdf",
                "资料",
                "business_knowledge",
                original_retention_approved=True,
                page_evidence_mode="required",
            )
        )
        self.assertEqual((response.status, response.code), ("registered", None))
        self.assertEqual(response.record.page_evidence_mode, "required")
        self.assertEqual(response.record.page_count, 1)
        self.assertEqual(response.record.page_artifacts[0].sha256, hashlib.sha256(PNG).hexdigest())
        self.assertTrue(any(event["action"] == "store_page_evidence" for event in response.evidence["events"]))
        self.assertEqual(response.evidence["page_evidence"]["status"], "complete")

    @mock.patch("shared.stage5_intake.render_page_evidence")
    @mock.patch("shared.stage5_intake.convert_to_markdown")
    def test_page_rendering_never_starts_before_retention_approval(self, convert, render) -> None:
        convert.return_value = MarkdownConversion("# PDF\n", "markitdown", "0.1.6")
        response = Stage5Intake(self.adapter).execute(
            IntakeRequest(TASK_ID, self.binding, "资料.pdf", b"pdf", "资料", page_evidence_mode="required")
        )
        self.assertEqual((response.status, response.code), ("exception", "privacy_approval_required"))
        render.assert_not_called()
        self.assertNotIn("store_original", self.adapter.calls)

    @mock.patch("shared.stage5_intake.render_page_evidence", side_effect=PageRendererUnavailable("missing"))
    @mock.patch("shared.stage5_intake.convert_to_markdown")
    def test_missing_optional_renderer_stops_without_source_writes(self, convert, _render) -> None:
        convert.return_value = MarkdownConversion("# PDF\n", "markitdown", "0.1.6")
        response = Stage5Intake(self.adapter).execute(
            IntakeRequest(
                TASK_ID,
                self.binding,
                "资料.pdf",
                b"pdf",
                "资料",
                original_retention_approved=True,
                page_evidence_mode="required",
            )
        )
        self.assertEqual((response.status, response.code), ("exception", "page_evidence_unavailable"))
        self.assertNotIn("store_original", self.adapter.calls)

    @mock.patch("shared.stage5_intake.render_page_evidence")
    @mock.patch("shared.stage5_intake.convert_to_markdown")
    def test_obsidian_page_evidence_is_isolated_by_source_and_read_back(self, convert, render) -> None:
        convert.return_value = MarkdownConversion("# PDF\n", "markitdown", "0.1.6")
        render.return_value = rendered()
        with tempfile.TemporaryDirectory() as folder:
            active_binding = binding(locator=folder)
            adapter = ObsidianAdapter()
            adapter.resolve_binding(active_binding)
            adapter.create_skeleton(active_binding)
            response = Stage5Intake(adapter).execute(
                IntakeRequest(
                    TASK_ID,
                    active_binding,
                    "资料.pdf",
                    b"pdf",
                    "资料",
                    original_retention_approved=True,
                    page_evidence_mode="required",
                )
            )
            self.assertEqual((response.status, response.code), ("registered", None))
            source_dir = Path(folder) / "01-来源索引" / response.record.display_name
            page = source_dir / "页面证据" / "第001页.png"
            self.assertEqual(page.read_bytes(), PNG)
            readable = source_dir / f"{response.record.display_name}-可读版.md"
            self.assertIn("![[页面证据/第001页.png]]", readable.read_text(encoding="utf-8"))
            self.assertEqual(adapter.read_back(active_binding, response.refs).status, "ok")
            fresh_adapter = ObsidianAdapter()
            fresh_adapter.resolve_binding(active_binding)
            routed = Stage6Knowledge(fresh_adapter).execute(
                KnowledgeRequest(TASK_ID, active_binding, response.record, "资料知识", "通用资料", "来源已经完整登记。")
            )
            self.assertEqual((routed.status, routed.code), ("registered", None))


class PageEvidenceStorageTests(unittest.TestCase):
    def test_feishu_page_name_is_human_readable_and_listed_after_upload(self) -> None:
        source_id = "SRC-" + hashlib.sha256(b"pdf").hexdigest()[:24]
        page = rendered().pages[0].artifact
        source = SourceRecord(
            SOURCE_SCHEMA,
            source_id,
            "CLT-1234567890ABCD",
            "资料",
            "business_knowledge",
            "document",
            "资料.pdf",
            hashlib.sha256(b"pdf").hexdigest(),
            hashlib.sha256(b"readable").hexdigest(),
            "passed",
            "allowed",
            None,
            "registered",
            True,
            page_evidence_mode="required",
            page_count=1,
            page_artifacts=(page,),
            display_name="2026-08-31 资料",
        )
        name = "2026-08-31 资料-第001页.png"
        list_call = ("lark-cli", "--as", "user", "wiki", "nodes", "list", "--space-id", "1", "--parent-node-token", "root-01", "--page-all", "--format", "json")
        upload_call = ("lark-cli", "--as", "user", "drive", "+upload", "--file", "{file}", "--wiki-token", "root-01", "--name", name, "--format", "json")
        runner = RecordedCliRunner((
            RecordedCliCall(list_call, '{"ok":true,"data":{"items":[]}}'),
            RecordedCliCall(upload_call, '{"ok":true,"data":{"file_token":"token"}}', payload=PNG, upload_name=name),
            RecordedCliCall(list_call, '{"ok":true,"data":{"items":[{"title":"' + name + '","obj_type":"file","obj_token":"token"}]}}'),
        ))
        result = FeishuStage5Storage(runner, "1", {"01": "root-01", "02": "root-02"}).store_page_evidence(source, page, PNG)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.object_refs[0].object_kind, "source_page")
        self.assertTrue(runner.exhausted)

    def test_feishu_human_named_source_can_be_found_in_a_fresh_run(self) -> None:
        source_id = "SRC-" + hashlib.sha256(b"pdf").hexdigest()[:24]
        display_name = "2026-08-31 资料"
        original_name = f"{display_name}-原件.pdf"
        readable_content = f'---\nsource_id: "{source_id}"\ndisplay_name: "{display_name}"\noriginal_file_name: "资料.pdf"\n---\n'
        list_call = ("lark-cli", "--as", "user", "wiki", "nodes", "list", "--space-id", "1", "--parent-node-token", "root-01", "--page-all", "--format", "json")
        fetch_call = ("lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", "readable-token", "--doc-format", "markdown", "--format", "json")
        runner = RecordedCliRunner((
            RecordedCliCall(list_call, '{"ok":true,"data":{"items":[{"title":"' + display_name + '","obj_type":"docx","obj_token":"readable-token"},{"title":"' + original_name + '","obj_type":"file","obj_token":"original-token"}]}}'),
            RecordedCliCall(fetch_call, json.dumps({"ok": True, "data": {"document": {"content": readable_content}}}, ensure_ascii=False)),
            RecordedCliCall(list_call, '{"ok":true,"data":{"items":[{"title":"' + original_name + '","obj_type":"file","obj_token":"original-token"}]}}'),
        ))
        result = FeishuStage5Storage(runner, "1", {"01": "root-01", "02": "root-02"}).registered_source(source_id, "business_knowledge")
        self.assertEqual(result.status, "ok")
        self.assertTrue(runner.exhausted)


class PageRendererContractTests(unittest.TestCase):
    @mock.patch(
        "shared.page_renderer._find_dependency",
        side_effect=lambda name: "tool" if name in {"pdftoppm", "pdfinfo"} else None,
    )
    @mock.patch("shared.page_renderer._find_mac_powerpoint_automation", return_value=None)
    @mock.patch(
        "shared.page_renderer._find_windows_powerpoint_automation",
        return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        create=True,
    )
    def test_windows_powerpoint_satisfies_pptx_renderer_status(self, _windows, _mac, _dependency) -> None:
        self.assertEqual(page_renderer.renderer_status(".pptx"), (True, ()))

    @mock.patch("shared.page_renderer._find_dependency", side_effect=lambda name: "/usr/bin/tool" if name in {"pdftoppm", "pdfinfo"} else None)
    @mock.patch("shared.page_renderer._find_mac_powerpoint_automation", return_value="/usr/bin/osascript")
    def test_mac_powerpoint_satisfies_pptx_renderer_status(self, _powerpoint, _dependency) -> None:
        self.assertEqual(page_renderer.renderer_status(".pptx"), (True, ()))

    @mock.patch("shared.page_renderer._pptx_to_pdf_with_libreoffice")
    @mock.patch("shared.page_renderer._pptx_to_pdf_with_powerpoint_windows", create=True)
    @mock.patch("shared.page_renderer._find_mac_powerpoint_automation", return_value=None)
    @mock.patch(
        "shared.page_renderer._find_windows_powerpoint_automation",
        return_value="powershell.exe",
        create=True,
    )
    def test_windows_powerpoint_is_preferred_for_pptx(
        self, _windows, _mac, power_point, libreoffice
    ) -> None:
        source = Path(r"C:\tmp\source.pptx")
        work_root = Path(r"C:\tmp")
        target = work_root / "source.pdf"
        power_point.return_value = target
        self.assertEqual(page_renderer._pptx_to_pdf(source, work_root), (target, "microsoft-powerpoint"))
        power_point.assert_called_once_with(source, work_root, "powershell.exe")
        libreoffice.assert_not_called()

    @mock.patch("shared.page_renderer._pptx_to_pdf_with_libreoffice")
    @mock.patch(
        "shared.page_renderer._pptx_to_pdf_with_powerpoint_windows",
        side_effect=PageRenderFailed("native failed"),
        create=True,
    )
    @mock.patch("shared.page_renderer._find_mac_powerpoint_automation", return_value=None)
    @mock.patch(
        "shared.page_renderer._find_windows_powerpoint_automation",
        return_value="powershell.exe",
        create=True,
    )
    def test_windows_native_failure_does_not_silently_fallback(
        self, _windows, _mac, _powerpoint, libreoffice
    ) -> None:
        with self.assertRaises(PageRenderFailed):
            page_renderer._pptx_to_pdf(Path(r"C:\tmp\source.pptx"), Path(r"C:\tmp"))
        libreoffice.assert_not_called()

    @mock.patch("shared.page_renderer._pptx_to_pdf_with_libreoffice")
    @mock.patch("shared.page_renderer._pptx_to_pdf_with_powerpoint_mac")
    @mock.patch("shared.page_renderer._find_mac_powerpoint_automation", return_value="/usr/bin/osascript")
    def test_mac_powerpoint_is_preferred_for_pptx(self, _automation, power_point, libreoffice) -> None:
        source = Path("/tmp/source.pptx")
        target = Path("/tmp/source.pdf")
        power_point.return_value = target
        self.assertEqual(page_renderer._pptx_to_pdf(source, Path("/tmp")), (target, "microsoft-powerpoint"))
        power_point.assert_called_once_with(source, Path("/tmp"), "/usr/bin/osascript")
        libreoffice.assert_not_called()

    @mock.patch("shared.page_renderer._pptx_to_pdf_with_libreoffice")
    @mock.patch("shared.page_renderer._pptx_to_pdf_with_powerpoint_mac", side_effect=PageRenderFailed("native failed"))
    @mock.patch("shared.page_renderer._find_mac_powerpoint_automation", return_value="/usr/bin/osascript")
    def test_native_powerpoint_failure_does_not_silently_fallback(self, _automation, _powerpoint, libreoffice) -> None:
        with self.assertRaises(PageRenderFailed):
            page_renderer._pptx_to_pdf(Path("/tmp/source.pptx"), Path("/tmp"))
        libreoffice.assert_not_called()

    def test_windows_powerpoint_timeout_cleans_up_recorded_process(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            work_root = Path(folder)
            source = work_root / "source.pptx"
            source.write_bytes(b"pptx")
            pid_path = work_root / "render-pptx.pid"

            def time_out(*_args, **_kwargs):
                pid_path.write_text("4242", encoding="ascii")
                raise subprocess.TimeoutExpired(("powershell.exe",), 180)

            with mock.patch("shared.page_renderer.subprocess.run", side_effect=time_out):
                with mock.patch(
                    "shared.page_renderer._terminate_recorded_powerpoint_process", create=True
                ) as terminate:
                    with self.assertRaisesRegex(PageRenderFailed, "timed out"):
                        page_renderer._pptx_to_pdf_with_powerpoint_windows(source, work_root, "powershell.exe")
            terminate.assert_called_once_with(pid_path)

    def test_windows_cleanup_terminates_only_the_recorded_process(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pid_path = Path(folder) / "render-pptx.pid"
            pid_path.write_text("4242", encoding="ascii")
            with mock.patch("shared.page_renderer.os.kill") as kill:
                page_renderer._terminate_recorded_powerpoint_process(pid_path)
            kill.assert_called_once_with(4242, signal.SIGTERM)
            self.assertFalse(pid_path.exists())

    @mock.patch("shared.page_renderer._pdf_page_count", return_value=2)
    @mock.patch("shared.page_renderer.renderer_status", return_value=(True, ()))
    @mock.patch("shared.page_renderer._find_dependency", return_value="pdftoppm")
    def test_missing_page_number_fails_closed(self, _dependency, _status, _count) -> None:
        def fake_run(argv, **_kwargs):
            prefix = Path(argv[-1])
            prefix.with_name(prefix.name + "-1.png").write_bytes(PNG)
            prefix.with_name(prefix.name + "-3.png").write_bytes(PNG)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as folder, mock.patch(
            "shared.page_renderer._run", side_effect=fake_run
        ):
            with self.assertRaises(PageRenderFailed):
                page_renderer.render_page_evidence(
                    b"pdf", ".pdf", "SRC-" + hashlib.sha256(b"pdf").hexdigest()[:24], Path(folder)
                )


if __name__ == "__main__":
    unittest.main()
