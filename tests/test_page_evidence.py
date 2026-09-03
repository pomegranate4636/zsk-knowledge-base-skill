from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, SOURCE_SCHEMA, Binding, KnowledgeFact, PageArtifact, SourceRecord  # noqa: E402
from shared.fake_adapter import FakeAdapter  # noqa: E402
from shared.feishu_cli import RecordedCliCall, RecordedCliRunner  # noqa: E402
from shared.feishu_stage5 import FeishuStage5Storage  # noqa: E402
from shared.markdown_converter import MarkdownConversion  # noqa: E402
from shared.ocr_provider import OcrResult  # noqa: E402
from shared.obsidian_adapter import ObsidianAdapter  # noqa: E402
from shared import page_renderer  # noqa: E402
from shared.page_renderer import PageRenderFailed, PageRendererUnavailable, RenderedPage, RenderedPages  # noqa: E402
from shared.stage5_intake import IntakeRequest, Stage5Intake  # noqa: E402
from shared.stage6_knowledge import KnowledgeRequest, Stage6Knowledge  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"
PNG = b"\x89PNG\r\n\x1a\nsynthetic-page"


class HighConfidenceOcr:
    name = "test-local-ocr"

    def recognize(self, _image: bytes) -> OcrResult:
        return OcrResult("页图文字", 0.99, self.name)


class LowConfidenceOcr:
    name = "test-low-confidence-ocr"

    def recognize(self, _image: bytes) -> OcrResult:
        return OcrResult("不可靠文字", 0.42, self.name)


class SensitiveOcr:
    name = "test-sensitive-ocr"

    def recognize(self, _image: bytes) -> OcrResult:
        return OcrResult("联系人 13800138000 test@example.com", 0.99, self.name)


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
        1600,
        900,
    )
    return RenderedPages((RenderedPage(artifact, payload),), 1, "pdftoppm")


def file_snapshot(root: str) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in Path(root).rglob("*")
        if path.is_file()
    }


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
        response = Stage5Intake(self.adapter, HighConfidenceOcr()).execute(
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
    def test_ocr_only_sensitive_text_is_redacted_before_any_write(self, convert, render) -> None:
        convert.return_value = MarkdownConversion("# 普通正文\n", "markitdown", "0.1.6")
        render.return_value = rendered()
        response = Stage5Intake(self.adapter, SensitiveOcr()).execute(
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
        self.assertEqual((response.status, response.code), ("registered", None))
        self.assertEqual(response.record.privacy_status, "redacted")
        readable = self.adapter._objects[f"source:{response.source_id}:readable"]["payload"].decode("utf-8")
        self.assertNotIn("13800138000", readable)
        self.assertNotIn("test@example.com", readable)
        self.assertIn("[已脱敏]", readable)
        self.assertEqual(
            response.evidence["page_text_evidence"]["evidence_sha256"],
            [item.evidence_sha256 for item in response.record.page_text_evidence],
        )

    def test_default_intake_allows_processing_but_requires_original_retention_approval(self) -> None:
        request = IntakeRequest(TASK_ID, self.binding, "资料.pdf", b"pdf", "资料")
        self.assertEqual(request.permission_status, "allowed")
        self.assertFalse(request.original_retention_approved)

    @mock.patch("shared.stage5_intake.render_page_evidence")
    @mock.patch("shared.stage5_intake.convert_to_markdown")
    def test_low_confidence_page_set_is_rejected_without_any_backend_write(self, convert, render) -> None:
        convert.return_value = MarkdownConversion("# PDF\n", "markitdown", "0.1.6")
        render.return_value = rendered()
        response = Stage5Intake(self.adapter, LowConfidenceOcr()).execute(
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
        self.assertEqual((response.status, response.code), ("exception", "file_quality_insufficient"))
        self.assertNotIn("store_original", self.adapter.calls)
        self.assertNotIn("write_exception", self.adapter.calls)

    @mock.patch("shared.stage5_intake.render_page_evidence")
    @mock.patch("shared.stage5_intake.convert_to_markdown")
    def test_page_rendering_never_starts_before_retention_approval(self, convert, render) -> None:
        convert.return_value = MarkdownConversion("# PDF\n", "markitdown", "0.1.6")
        response = Stage5Intake(self.adapter).execute(
            IntakeRequest(
                TASK_ID,
                self.binding,
                "资料.pdf",
                b"pdf",
                "资料",
                original_retention_approved=False,
                page_evidence_mode="required",
            )
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
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(locator=folder)
            adapter = ObsidianAdapter()
            adapter.resolve_binding(active_binding)
            adapter.create_skeleton(active_binding)
            response = Stage5Intake(adapter, HighConfidenceOcr()).execute(
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
            text_evidence = response.record.page_text_evidence[0]
            routed = Stage6Knowledge(fresh_adapter).execute(
                KnowledgeRequest(
                    TASK_ID,
                    active_binding,
                    response.record,
                    "资料知识",
                    "通用资料",
                    "来源已经完整登记。",
                    fact_evidence=(KnowledgeFact("来源已经完整登记。", 1, "页图文字", text_evidence.evidence_sha256),),
                )
            )
            self.assertEqual((routed.status, routed.code), ("registered", None))

    def test_fresh_obsidian_intake_reuses_human_named_page_source_without_reconversion(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(locator=folder)
            first_adapter = ObsidianAdapter()
            first_adapter.resolve_binding(active_binding)
            first_adapter.create_skeleton(active_binding)
            request = IntakeRequest(
                TASK_ID,
                active_binding,
                "资料.pdf",
                b"pdf",
                "资料",
                "business_knowledge",
                original_retention_approved=True,
                page_evidence_mode="required",
            )
            with mock.patch("shared.stage5_intake.convert_to_markdown") as convert, mock.patch(
                "shared.stage5_intake.render_page_evidence"
            ) as render:
                convert.return_value = MarkdownConversion("# PDF\n", "markitdown", "0.1.6")
                render.return_value = rendered()
                first = Stage5Intake(first_adapter, HighConfidenceOcr()).execute(request)
            self.assertEqual((first.status, first.code), ("registered", None))
            before = file_snapshot(folder)

            fresh_adapter = ObsidianAdapter()
            fresh_adapter.resolve_binding(active_binding)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                side_effect=AssertionError("existing source must not be reconverted"),
            ), mock.patch(
                "shared.stage5_intake.render_page_evidence",
                side_effect=AssertionError("existing pages must not be rerendered"),
            ):
                reused = Stage5Intake(fresh_adapter).execute(request)
            after = file_snapshot(folder)
            self.assertEqual((reused.status, reused.code), ("reused", None))
            self.assertEqual(reused.evidence["existing_layout_reused"], "human_named")
            self.assertEqual(reused.record.source_id, first.source_id)
            self.assertEqual(reused.record.page_text_evidence, first.record.page_text_evidence)
            self.assertEqual(reused.refs, first.refs)
            self.assertIn(f"/{first.record.display_name}/页面证据/第001页.png", reused.refs[-1].locator)
            text_evidence = reused.record.page_text_evidence[0]
            routed = Stage6Knowledge(fresh_adapter).execute(
                KnowledgeRequest(
                    TASK_ID,
                    active_binding,
                    reused.record,
                    "资料知识",
                    "通用资料",
                    "来源已经完整登记。",
                    fact_evidence=(
                        KnowledgeFact(
                            "来源已经完整登记。",
                            1,
                            "页图文字",
                            text_evidence.evidence_sha256,
                        ),
                    ),
                )
            )
            self.assertEqual((routed.status, routed.code), ("registered", None))
            self.assertEqual(before, after)

    def test_fresh_obsidian_reuse_rejects_another_client_without_writes(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            owner = binding(locator=folder)
            owner_adapter = ObsidianAdapter()
            owner_adapter.resolve_binding(owner)
            owner_adapter.create_skeleton(owner)
            request = IntakeRequest(TASK_ID, owner, "资料.pdf", b"pdf", "资料", original_retention_approved=True)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                return_value=MarkdownConversion("# PDF\n", "markitdown", "0.1.6"),
            ):
                first = Stage5Intake(owner_adapter).execute(request)
            self.assertEqual((first.status, first.code), ("registered", None))
            before = file_snapshot(folder)
            foreign = Binding(
                BINDING_SCHEMA,
                "CLT-OTHER-1234567890",
                "其他客户",
                "其他知识库",
                "company",
                "obsidian",
                folder,
                {key: f"root:{key}" for key in ROOT_KEYS},
                TEMPLATE_VERSION,
            )
            foreign_adapter = ObsidianAdapter()
            foreign_adapter.resolve_binding(foreign)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                side_effect=AssertionError("cross-client source must not be converted or reused"),
            ):
                rejected = Stage5Intake(foreign_adapter).execute(
                    IntakeRequest(TASK_ID, foreign, "资料.pdf", b"pdf", "资料", original_retention_approved=True)
                )
            self.assertEqual((rejected.status, rejected.code), ("exception", "binding_conflict"))
            self.assertIsNone(rejected.record)
            self.assertFalse(any(event["action"] == "write_exception" for event in rejected.evidence["events"]))
            self.assertEqual(before, file_snapshot(folder))

    def test_fresh_obsidian_reuse_rejects_missing_client_identity_without_writes(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(locator=folder)
            first_adapter = ObsidianAdapter()
            first_adapter.resolve_binding(active_binding)
            first_adapter.create_skeleton(active_binding)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                return_value=MarkdownConversion("# PDF\n", "markitdown", "0.1.6"),
            ):
                first = Stage5Intake(first_adapter).execute(
                    IntakeRequest(TASK_ID, active_binding, "资料.pdf", b"pdf", "资料", original_retention_approved=True)
                )
            readable = next((Path(folder) / "01-来源索引" / first.record.display_name).glob("*-可读版.md"))
            content = readable.read_text(encoding="utf-8")
            readable.write_text(
                "\n".join(line for line in content.splitlines() if not line.startswith("client_id: ")) + "\n",
                encoding="utf-8",
            )
            before = file_snapshot(folder)
            fresh_adapter = ObsidianAdapter()
            fresh_adapter.resolve_binding(active_binding)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                side_effect=AssertionError("unbound source must not be converted or reused"),
            ):
                rejected = Stage5Intake(fresh_adapter).execute(
                    IntakeRequest(
                        TASK_ID,
                        active_binding,
                        "资料.pdf",
                        b"pdf",
                        "资料",
                        original_retention_approved=True,
                    )
                )
            self.assertEqual((rejected.status, rejected.code), ("exception", "binding_conflict"))
            self.assertFalse(any(event["action"] == "write_exception" for event in rejected.evidence["events"]))
            self.assertEqual(before, file_snapshot(folder))

    def test_text_only_source_cannot_satisfy_required_page_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(locator=folder)
            first_adapter = ObsidianAdapter()
            first_adapter.resolve_binding(active_binding)
            first_adapter.create_skeleton(active_binding)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                return_value=MarkdownConversion("# PDF\n", "markitdown", "0.1.6"),
            ):
                first = Stage5Intake(first_adapter).execute(
                    IntakeRequest(TASK_ID, active_binding, "资料.pdf", b"pdf", "资料", original_retention_approved=True)
                )
            self.assertEqual(first.record.page_evidence_mode, "off")
            fresh_adapter = ObsidianAdapter()
            fresh_adapter.resolve_binding(active_binding)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                side_effect=AssertionError("registered source must not be reconverted"),
            ), mock.patch(
                "shared.stage5_intake.render_page_evidence",
                side_effect=AssertionError("incomplete source must not be reported as reused"),
            ):
                rejected = Stage5Intake(fresh_adapter).execute(
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
            self.assertEqual((rejected.status, rejected.code), ("exception", "page_evidence_failed"))
            self.assertTrue(any(event["action"] == "write_exception" for event in rejected.evidence["events"]))

    def test_existing_required_source_still_needs_current_retention_approval(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(locator=folder)
            first_adapter = ObsidianAdapter()
            first_adapter.resolve_binding(active_binding)
            first_adapter.create_skeleton(active_binding)
            approved = IntakeRequest(
                TASK_ID,
                active_binding,
                "资料.pdf",
                b"pdf",
                "资料",
                original_retention_approved=True,
                page_evidence_mode="required",
            )
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                return_value=MarkdownConversion("# PDF\n", "markitdown", "0.1.6"),
            ), mock.patch("shared.stage5_intake.render_page_evidence", return_value=rendered()):
                first = Stage5Intake(first_adapter, HighConfidenceOcr()).execute(approved)
            self.assertEqual((first.status, first.code), ("registered", None))
            fresh_adapter = ObsidianAdapter()
            fresh_adapter.resolve_binding(active_binding)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                side_effect=AssertionError("approval gate must run before conversion or reuse"),
            ), mock.patch(
                "shared.stage5_intake.render_page_evidence",
                side_effect=AssertionError("approval gate must run before rendering"),
            ):
                rejected = Stage5Intake(fresh_adapter).execute(
                    IntakeRequest(TASK_ID, active_binding, "资料.pdf", b"pdf", "资料", page_evidence_mode="required")
                )
            self.assertEqual((rejected.status, rejected.code), ("exception", "privacy_approval_required"))
            self.assertEqual(rejected.evidence["page_evidence"], {"mode": "required", "status": "approval_required"})

    def test_existing_required_source_cannot_bypass_approval_by_requesting_off(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(locator=folder)
            first_adapter = ObsidianAdapter()
            first_adapter.resolve_binding(active_binding)
            first_adapter.create_skeleton(active_binding)
            approved = IntakeRequest(
                TASK_ID,
                active_binding,
                "资料.pdf",
                b"pdf",
                "资料",
                original_retention_approved=True,
                page_evidence_mode="required",
            )
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                return_value=MarkdownConversion("# PDF\n", "markitdown", "0.1.6"),
            ), mock.patch("shared.stage5_intake.render_page_evidence", return_value=rendered()):
                first = Stage5Intake(first_adapter, HighConfidenceOcr()).execute(approved)
            self.assertEqual((first.status, first.code), ("registered", None))

            fresh_adapter = ObsidianAdapter()
            fresh_adapter.resolve_binding(active_binding)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                side_effect=AssertionError("approval gate must run before reconversion"),
            ), mock.patch(
                "shared.stage5_intake.render_page_evidence",
                side_effect=AssertionError("approval gate must run before rerendering"),
            ):
                rejected = Stage5Intake(fresh_adapter).execute(
                    IntakeRequest(TASK_ID, active_binding, "资料.pdf", b"pdf", "资料")
                )
            self.assertEqual((rejected.status, rejected.code), ("exception", "privacy_approval_required"))
            self.assertIsNone(rejected.record)
            self.assertFalse(any(ref.object_kind == "source_page" for ref in rejected.refs))

            approved_off_adapter = ObsidianAdapter()
            approved_off_adapter.resolve_binding(active_binding)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                side_effect=AssertionError("approved existing source must not be reconverted"),
            ), mock.patch(
                "shared.stage5_intake.render_page_evidence",
                side_effect=AssertionError("approved existing pages must not be rerendered"),
            ):
                reused = Stage5Intake(approved_off_adapter).execute(
                    IntakeRequest(
                        TASK_ID,
                        active_binding,
                        "资料.pdf",
                        b"pdf",
                        "资料",
                        original_retention_approved=True,
                    )
                )
            self.assertEqual((reused.status, reused.code), ("reused", None))
            self.assertEqual(reused.record.page_evidence_mode, "required")
            self.assertTrue(any(ref.object_kind == "source_page" for ref in reused.refs))

    def test_reuse_rejects_page_manifest_with_wrong_source_identity(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(locator=folder)
            first_adapter = ObsidianAdapter()
            first_adapter.resolve_binding(active_binding)
            first_adapter.create_skeleton(active_binding)
            request = IntakeRequest(
                TASK_ID,
                active_binding,
                "资料.pdf",
                b"pdf",
                "资料",
                original_retention_approved=True,
                page_evidence_mode="required",
            )
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                return_value=MarkdownConversion("# PDF\n", "markitdown", "0.1.6"),
            ), mock.patch("shared.stage5_intake.render_page_evidence", return_value=rendered()):
                first = Stage5Intake(first_adapter, HighConfidenceOcr()).execute(request)
            readable = next((Path(folder) / "01-来源索引" / first.record.display_name).glob("*-可读版.md"))
            content = readable.read_text(encoding="utf-8")
            content = content.replace(
                f'"source_id": "{first.source_id}"',
                '"source_id": "SRC-000000000000000000000000"',
                1,
            )
            readable.write_text(content, encoding="utf-8")
            fresh_adapter = ObsidianAdapter()
            fresh_adapter.resolve_binding(active_binding)
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                side_effect=AssertionError("invalid manifest must fail before reconversion"),
            ), mock.patch(
                "shared.stage5_intake.render_page_evidence",
                side_effect=AssertionError("invalid manifest must fail before rerendering"),
            ):
                rejected = Stage5Intake(fresh_adapter).execute(request)
            self.assertEqual((rejected.status, rejected.code), ("exception", "readback_failed"))

    def test_reuse_rejects_non_contiguous_page_number(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(locator=folder)
            first_adapter = ObsidianAdapter()
            first_adapter.resolve_binding(active_binding)
            first_adapter.create_skeleton(active_binding)
            request = IntakeRequest(
                TASK_ID,
                active_binding,
                "资料.pdf",
                b"pdf",
                "资料",
                original_retention_approved=True,
                page_evidence_mode="required",
            )
            with mock.patch(
                "shared.stage5_intake.convert_to_markdown",
                return_value=MarkdownConversion("# PDF\n", "markitdown", "0.1.6"),
            ), mock.patch("shared.stage5_intake.render_page_evidence", return_value=rendered()):
                first = Stage5Intake(first_adapter, HighConfidenceOcr()).execute(request)
            readable = next((Path(folder) / "01-来源索引" / first.record.display_name).glob("*-可读版.md"))
            content = readable.read_text(encoding="utf-8")
            content = content.replace('"page_number": 1', '"page_number": 2', 1)
            readable.write_text(content, encoding="utf-8")
            fresh_adapter = ObsidianAdapter()
            fresh_adapter.resolve_binding(active_binding)
            reused = Stage5Intake(fresh_adapter).execute(request)
            self.assertEqual((reused.status, reused.code), ("exception", "readback_failed"))


class PageEvidenceStorageTests(unittest.TestCase):
    def test_feishu_page_is_not_uploaded_as_a_sibling_drive_file(self) -> None:
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
        list_call = ("lark-cli", "--as", "user", "wiki", "nodes", "list", "--space-id", "1", "--parent-node-token", "root-01", "--page-all", "--format", "json")
        runner = RecordedCliRunner((
            RecordedCliCall(list_call, '{"ok":true,"data":{"items":[]}}'),
        ))
        result = FeishuStage5Storage(runner, "1", {"01": "root-01", "02": "root-02"}).store_page_evidence(source, page, PNG)
        self.assertEqual((result.status, result.code), ("blocked", "ownership_unknown"))
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

    @mock.patch(
        "shared.page_renderer._find_dependency",
        side_effect=lambda name: "tool" if name in {"pdftoppm", "pdfinfo"} else None,
    )
    @mock.patch("shared.page_renderer._find_mac_powerpoint_automation", return_value=None)
    @mock.patch("shared.page_renderer._find_windows_powerpoint_automation", return_value=None)
    def test_pptx_status_requires_libreoffice_without_native_powerpoint(
        self, _windows, _mac, _dependency
    ) -> None:
        self.assertEqual(page_renderer.renderer_status(".pptx"), (False, ("soffice/libreoffice",)))

    def test_windows_powerpoint_detection_fails_closed(self) -> None:
        with mock.patch.object(page_renderer.platform, "system", return_value="Linux"):
            self.assertIsNone(page_renderer._find_windows_powerpoint_automation())
        with mock.patch.object(page_renderer.platform, "system", return_value="Windows"):
            with mock.patch.object(page_renderer.shutil, "which", return_value=None):
                self.assertIsNone(page_renderer._find_windows_powerpoint_automation())
        with mock.patch.object(page_renderer.platform, "system", return_value="Windows"):
            with mock.patch.object(page_renderer.shutil, "which", return_value="powershell.exe"):
                with mock.patch.object(
                    page_renderer.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired(("powershell.exe",), 15),
                ):
                    self.assertIsNone(page_renderer._find_windows_powerpoint_automation())

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
            terminate.assert_called_once_with(pid_path, "powershell.exe")

    def test_windows_cleanup_terminates_only_the_recorded_process(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pid_path = Path(folder) / "render-pptx.pid"
            pid_path.write_text(
                '{"pid":4242,"process_name":"POWERPNT","start_time_utc":"2026-09-02T00:00:00.0000000Z"}',
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(("powershell.exe",), 0, "", "")
            with mock.patch("shared.page_renderer.subprocess.run", return_value=completed) as run:
                page_renderer._terminate_recorded_powerpoint_process(pid_path, "powershell.exe")
            argv = run.call_args.args[0]
            self.assertIn("cleanup-pptx.ps1", str(argv))
            self.assertIn(str(pid_path), argv)
            self.assertFalse(pid_path.exists())

    @unittest.skipUnless(sys.platform == "win32", "requires a real Windows process table")
    def test_windows_cleanup_checks_real_process_name_and_start_time(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        self.assertIsNotNone(powershell)
        with tempfile.TemporaryDirectory() as folder:
            fake_powerpoint = Path(folder) / "POWERPNT.exe"
            shutil.copy2(Path(os.environ["WINDIR"]) / "System32" / "ping.exe", fake_powerpoint)
            process = subprocess.Popen(
                (str(fake_powerpoint), "127.0.0.1", "-t"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                identity_command = (
                    f"$p=Get-Process -Id {process.pid};"
                    "$v=[ordered]@{pid=[int]$p.Id;process_name=[string]$p.ProcessName;"
                    "start_time_utc=$p.StartTime.ToUniversalTime().ToString('o')};"
                    "$v|ConvertTo-Json -Compress"
                )
                completed = subprocess.run(
                    (powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", identity_command),
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                    shell=False,
                    encoding="utf-8",
                    errors="replace",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                identity = json.loads(completed.stdout.strip())
                self.assertEqual(str(identity["process_name"]).upper(), "POWERPNT")
                identity_path = Path(folder) / "render-pptx.pid"
                identity_path.write_text(json.dumps(identity), encoding="utf-8")
                page_renderer._terminate_recorded_powerpoint_process(identity_path, str(powershell))
                process.wait(timeout=15)
                self.assertIsNotNone(process.returncode)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)

    @mock.patch("shared.page_renderer._pdf_page_count", return_value=2)
    @mock.patch("shared.page_renderer.renderer_status", return_value=(True, ()))
    @mock.patch("shared.page_renderer._find_dependency", return_value="pdftoppm")
    def test_missing_page_number_fails_closed(self, _dependency, _status, _count) -> None:
        def fake_run(argv, **_kwargs):
            prefix = Path(argv[-1])
            prefix.with_name(prefix.name + "-1.png").write_bytes(PNG)
            prefix.with_name(prefix.name + "-3.png").write_bytes(PNG)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder, mock.patch(
            "shared.page_renderer._run", side_effect=fake_run
        ):
            with self.assertRaises(PageRenderFailed):
                page_renderer.render_page_evidence(
                    b"pdf", ".pdf", "SRC-" + hashlib.sha256(b"pdf").hexdigest()[:24], Path(folder)
                )


if __name__ == "__main__":
    unittest.main()
