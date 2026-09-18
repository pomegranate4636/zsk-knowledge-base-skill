"""Behavioral regressions for the installed shared fixes migrated into ZSK."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))
sys.path.insert(0, str(ROOT / "tests"))

from shared import content_source_contract as contract
from shared import content_koubo_slim_handoff as handoff
from shared.obsidian_adapter import ObsidianAdapter, canonical_obsidian_locator
from shared.ocr_provider import AutoOcrProvider
from shared.page_text import build_page_text_evidence
from shared.stage11_bootstrap import BootstrapRequest, FirstRunBootstrap
from shared.stage2_router import RouterRequest, Stage2Router, stable_client_id
from shared.stage5_intake import IntakeRequest, Stage5Intake
from test_content_koubo_slim_handoff import VALID_METHOD
from test_page_evidence import binding, rendered, HighConfidenceOcr, LowConfidenceOcr
from test_page_text_evidence import FakeOcr, page, pptx_payload, SOURCE_ID, PNG_1, PNG_2
from shared.fake_adapter import FakeAdapter
from shared.markdown_converter import MarkdownConversion
from shared.contracts import KnowledgeFact
from shared.stage6_knowledge import KnowledgeRequest, Stage6Knowledge

TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"
OTHER_TASK = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33c"


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class LocalContractRegressions(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        (self.vault / "06-Agent与Workflow").mkdir()
        self.manifest = contract.build_base_manifest(
            client_id="CLT-SAVED-CUSTOMER", knowledge_base_name="测试库",
            backend="obsidian", locator=str(self.vault),
        )
        self.index = contract.build_empty_profile_index(knowledge_base_id=self.manifest["knowledge_base_id"])
        self.manifest_path, self.index_path = contract.write_obsidian_base_contract(self.vault, self.manifest, self.index)
        self.registry = self.root / "host" / "registry.json"

    def plan(self, **changes):
        fields = dict(manifest=self.manifest, manifest_ref=contract.MANIFEST_RELATIVE_PATH.as_posix(),
                      profile_index_ref=contract.PROFILE_INDEX_RELATIVE_PATH.as_posix(),
                      workflows=("content-koubo-slim",), registry_path=self.registry)
        return contract.plan_registry_binding(**{**fields, **changes})

    def test_contract_changes_after_preview_preserve_existing_registry(self):
        initial = self.plan()
        contract.commit_registry_plan(initial, initial.confirmation)
        saved_registry = self.registry.read_bytes()
        for path in (self.manifest_path, self.index_path):
            with self.subTest(path=path.name):
                preview = self.plan()
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.assertRaises(contract.ContentSourceContractError):
                    contract.commit_registry_plan(preview, preview.confirmation)
                self.assertEqual(self.registry.read_bytes(), saved_registry)
                path.write_bytes(original)

    def test_missing_contract_after_preview_creates_no_registry(self):
        plan = self.plan()
        self.index_path.unlink()
        with self.assertRaises(contract.ContentSourceContractError):
            contract.commit_registry_plan(plan, plan.confirmation)
        self.assertFalse(self.registry.parent.exists())

    def test_preview_rejects_missing_malformed_or_foreign_index(self):
        original = self.index_path.read_bytes()
        for payload in (None, b"not json", json.dumps({**self.index, "knowledge_base_id": "KB-" + "F" * 16}).encode()):
            with self.subTest(payload=payload):
                if payload is None:
                    self.index_path.unlink()
                else:
                    self.index_path.write_bytes(payload)
                with self.assertRaises(contract.ContentSourceContractError):
                    self.plan()
                self.assertFalse(self.registry.exists())
                self.index_path.write_bytes(original)

    def test_preview_rejects_manifest_or_index_reference_mismatch(self):
        other = self.index_path.with_name("other.json")
        other.write_bytes(self.index_path.read_bytes())
        for changes in ({"manifest": {**self.manifest, "knowledge_base_name": "changed"}},
                        {"profile_index_ref": "06-Agent与Workflow/other.json"}):
            with self.subTest(changes=changes), self.assertRaises(contract.ContentSourceContractError):
                self.plan(**changes)
        self.assertFalse(self.registry.exists())

    def test_unsafe_references_are_rejected_by_manifest_and_registry(self):
        plan = self.plan()
        for reference in ("../outside.json", "/absolute.json", "C:/outside.json", "C:outside.json",
                          "06//index.json", "./index.json", "06/./index.json", "index.json ", " index.json", "06\\index.json"):
            with self.subTest(reference=reference):
                with self.assertRaises(contract.ContentSourceContractError):
                    contract.validate_manifest({**self.manifest, "profile_index_ref": reference})
                value = json.loads(json.dumps(plan.registry))
                value["bindings"][plan.binding_id]["manifest_ref"] = reference
                with self.assertRaises(contract.ContentSourceContractError):
                    contract.validate_registry(value)

    def test_reparse_point_is_rejected_before_reading_contract(self):
        real_lstat = os.lstat
        def lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if Path(path) == self.manifest_path.parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return info
        with mock.patch.object(contract.os, "lstat", side_effect=lstat):
            with self.assertRaises(contract.ContentSourceContractError):
                self.plan()
        self.assertFalse(self.registry.exists())

    def test_symlink_contract_is_rejected(self):
        alternate = self.root / "alternate.json"
        alternate.write_bytes(self.index_path.read_bytes())
        self.index_path.unlink()
        try:
            self.index_path.symlink_to(alternate)
        except OSError as exc:
            self.skipTest(f"symlink privilege unavailable: {exc}")
        with self.assertRaises(contract.ContentSourceContractError):
            self.plan()
        self.assertFalse(self.registry.exists())

    def test_saved_identity_is_reused_by_router_without_writes(self):
        self.assertNotEqual(stable_client_id(str(self.vault)), self.manifest["client_id"])
        before = snapshot(self.vault)
        response = Stage2Router(ObsidianAdapter()).execute(RouterRequest(
            TASK_ID, "检查状态", "obsidian", str(self.vault), "客户", "测试库", "company"))
        self.assertEqual(response.client_id, self.manifest["client_id"])
        self.assertEqual(snapshot(self.vault), before)

    def test_invalid_or_relocated_identity_blocks_router_before_adapter_calls(self):
        foreign = contract.build_base_manifest(client_id="CLT-OTHER", knowledge_base_name="其他库",
                                               backend="obsidian", locator=str(self.root / "elsewhere"))
        for payload in (b"broken", json.dumps(foreign).encode()):
            with self.subTest(payload=payload):
                self.manifest_path.write_bytes(payload)
                before = snapshot(self.vault)
                adapter = FakeAdapter()
                result = Stage2Router(adapter).execute(RouterRequest(
                    TASK_ID, "创建知识库", "obsidian", str(self.vault), "客户", "测试库", "company"))
                self.assertEqual((result.status, result.code), ("blocked", "binding_conflict"))
                self.assertEqual(adapter.calls, [])
                self.assertEqual(snapshot(self.vault), before)

    def test_legacy_vault_without_manifest_retains_derived_identity(self):
        self.manifest_path.unlink()
        response = Stage2Router(ObsidianAdapter()).execute(RouterRequest(
            TASK_ID, "检查状态", "obsidian", str(self.vault), "客户", "测试库", "company"))
        self.assertEqual(response.client_id, stable_client_id(str(self.vault)))


class HandoffEligibilityRegressions(unittest.TestCase):
    def test_h1_frontmatter_is_accepted_but_body_metadata_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            methods, profiles = root / "04", root / "05"
            methods.mkdir()
            profiles.mkdir()
            card = methods / "method.md"
            for prefix, expected in (("", 1), ("# 标题\n\n", 1), ("正文\n", 0),
                                     ("# 标题\n\n正文\n", 0), ("## 二级标题\n", 0)):
                with self.subTest(prefix=prefix):
                    card.write_text(prefix + VALID_METHOD.replace("oral_method_asset", "viral_template_deconstruction"), encoding="utf-8")
                    self.assertEqual(handoff._asset_counts(methods, profiles), (expected, 1 - expected, 0))

    def test_source_prohibitions_and_workflow_restrictions_filter_active_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            methods, profiles = root / "04", root / "05"
            methods.mkdir()
            profiles.mkdir()
            card = methods / "method.md"
            cases = [("do_not_use: true", 0), ("usage_policy: forbidden", 0),
                     ('usage_policy: {"status": "forbidden"}', 0),
                     ("maturity: deprecated", 0), ("claim_scope: BLOCKED", 0),
                     ("source_verification: rejected", 0), ("usage_scope: disabled", 0),
                     ("applicable_workflows:\n  - content-gzh-slim", 0),
                     ("applicable_workflows:\n  - content-koubo-slim", 1),
                     ("usage_scope: method_reference_only", 1), ("", 1)]
            for kind in ("oral_method_asset", "content_method_asset", "viral_template_deconstruction"):
                for fields, expected in cases:
                    with self.subTest(kind=kind, fields=fields):
                        card.write_text(VALID_METHOD.replace("oral_method_asset", kind).replace("status: active", "status: active\n" + fields), encoding="utf-8")
                        self.assertEqual(handoff._asset_counts(methods, profiles), (expected, 1 - expected, 0))


class OcrApprovalRegressions(unittest.TestCase):
    def gate(self):
        from shared.ocr_review import OcrReviewGate
        return OcrReviewGate()

    def scope(self):
        return (TASK_ID, SOURCE_ID, 2, hashlib.sha256(PNG_2).hexdigest(), "校对后的文字")

    def test_receipt_requires_confirmation_is_scope_bound_and_single_use(self):
        gate = self.gate()
        scope = self.scope()
        preview = gate.preview(*scope)
        self.assertFalse(gate.consume(preview.confirmation, *scope))
        receipt = gate.confirm(preview.confirmation)
        with self.assertRaises(ValueError):
            gate.confirm(preview.confirmation)
        for position, changed in enumerate((OTHER_TASK, "SRC-" + "f" * 24, 3, "f" * 64, "另一个修改")):
            altered = list(scope)
            altered[position] = changed
            self.assertFalse(gate.consume(receipt, *altered))
        self.assertFalse(self.gate().consume(receipt, *scope))
        self.assertTrue(gate.consume(receipt, *scope))
        self.assertFalse(gate.consume(receipt, *scope))

    def test_pending_and_approved_receipts_expire(self):
        gate = self.gate()
        with mock.patch("shared.ocr_review.time.monotonic", return_value=100):
            pending = gate.preview(*self.scope())
            receipt = gate.confirm(gate.preview(*self.scope()).confirmation)
        with mock.patch("shared.ocr_review.time.monotonic", return_value=1000):
            with self.assertRaises(ValueError):
                gate.confirm(pending.confirmation)
            self.assertFalse(gate.consume(receipt, *self.scope()))

    def test_correction_without_approved_receipt_does_not_become_evidence(self):
        evidence = build_page_text_evidence(SOURCE_ID, ".pptx", pptx_payload(),
            (page(1, PNG_1), page(2, PNG_2)), FakeOcr(0.99), corrections={2: "unapproved"})
        self.assertEqual((evidence[1].review_status, evidence[1].verbatim_text), ("review_required", ""))

    def test_single_high_confidence_provider_cannot_auto_verify(self):
        evidence = build_page_text_evidence(SOURCE_ID, ".pptx", pptx_payload(),
            (page(1, PNG_1), page(2, PNG_2)), FakeOcr(0.99))
        self.assertEqual((evidence[1].review_status, evidence[1].verbatim_text), ("review_required", ""))
        self.assertEqual(evidence[0].review_status, "verified_native")

    def test_thresholds_cannot_weaken_quality_floor(self):
        for value in (-1, 0, 0.919, 1.1, float("nan")):
            with self.subTest(agreement=value), self.assertRaises(ValueError):
                AutoOcrProvider((FakeOcr(0.99), FakeOcr(0.99)), agreement_threshold=value)
        for value in (-1, 0, 0.849, 1.1, float("nan")):
            with self.subTest(confidence=value), self.assertRaises(ValueError):
                build_page_text_evidence(SOURCE_ID, ".pptx", pptx_payload(),
                    (page(1, PNG_1), page(2, PNG_2)), FakeOcr(0.99), confidence_threshold=value)

    def test_stage5_requires_receipt_and_propagates_reviewed_correction(self):
        gate = self.gate()
        active_binding = binding()
        image = rendered().pages[0]
        correction = "经确认的页图文字"
        source_id = "SRC-" + hashlib.sha256(b"pdf").hexdigest()[:24]
        receipt = gate.confirm(gate.preview(TASK_ID, source_id, 1, image.artifact.sha256, correction).confirmation)
        for approved, expected in ((False, "exception"), (True, "registered"), (True, "exception")):
            with self.subTest(approved=approved, expected=expected):
                adapter = FakeAdapter()
                adapter.resolve_binding(active_binding)
                adapter.create_skeleton(active_binding)
                with mock.patch("shared.stage5_intake.render_page_evidence", return_value=rendered()), \
                     mock.patch("shared.stage5_intake.convert_to_markdown", return_value=MarkdownConversion("# PDF\n", "markitdown", "0.1.6")):
                    response = Stage5Intake(adapter, LowConfidenceOcr(), ocr_review_gate=gate).execute(IntakeRequest(
                        TASK_ID, active_binding, "file.pdf", b"pdf", "文件", original_retention_approved=True,
                        page_evidence_mode="required", ocr_corrections={1: correction},
                        ocr_correction_approvals={1: receipt} if approved else {}))
                self.assertEqual(response.status, expected)
                if expected == "registered":
                    self.assertEqual(response.record.page_text_evidence[0].verbatim_text, correction)
                    self.assertEqual(response.record.page_text_evidence[0].review_status, "approved")
                else:
                    self.assertNotIn("store_original", adapter.calls)
                    self.assertNotIn("write_exception", adapter.calls)

    def test_approval_must_reference_a_proposed_correction(self):
        with self.assertRaises(ValueError):
            IntakeRequest(TASK_ID, binding(), "file.pdf", b"pdf", "文件", ocr_correction_approvals={1: "receipt"})


class LocalDirectoryRegressions(unittest.TestCase):
    def test_source_page_and_knowledge_directories_use_platform_mode(self):
        expected = 0o777 if os.name == "nt" else 0o700
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            active_binding = binding(locator=str(root))
            adapter = ObsidianAdapter()
            self.assertEqual(adapter.resolve_binding(active_binding).status, "ok")
            self.assertEqual(adapter.create_skeleton(active_binding).status, "ok")
            real_mkdir = os.mkdir
            with mock.patch("os.mkdir", wraps=real_mkdir) as mkdir, \
                 mock.patch("shared.stage5_intake.render_page_evidence", return_value=rendered()), \
                 mock.patch("shared.stage5_intake.convert_to_markdown", return_value=MarkdownConversion("# PDF\n", "markitdown", "0.1.6")):
                response = Stage5Intake(adapter, AutoOcrProvider((HighConfidenceOcr(), HighConfidenceOcr()))).execute(
                    IntakeRequest(TASK_ID, active_binding, "资料.pdf", b"pdf", "资料", "business_knowledge",
                                  original_retention_approved=True, page_evidence_mode="required"))
                self.assertEqual(response.status, "registered")
                evidence = response.record.page_text_evidence[0]
                result = Stage6Knowledge(adapter).execute(KnowledgeRequest(
                    TASK_ID, active_binding, response.record, "知识卡", "测试主题", "页图文字",
                    fact_evidence=(KnowledgeFact("页图文字", 1, evidence.verbatim_text, evidence.evidence_sha256),)))
            self.assertEqual(result.status, "registered")
            modes = {Path(call.args[0]): call.args[1] if len(call.args) > 1 else call.kwargs.get("mode", 0o777)
                     for call in mkdir.call_args_list}
            source_dirs = list((root / "01-来源索引").iterdir())
            self.assertEqual(len(source_dirs), 1)
            for path in (source_dirs[0], source_dirs[0] / "页面证据", root / "03-业务知识库" / "测试主题"):
                self.assertEqual(modes[path], expected, path)

    def test_windows_reparse_locator_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            real_lstat = os.lstat
            def lstat(path, *args, **kwargs):
                if Path(path) == root:
                    return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                return real_lstat(path, *args, **kwargs)
            with mock.patch("shared.obsidian_adapter.os.lstat", side_effect=lstat):
                self.assertIsNone(canonical_obsidian_locator(str(root)))

    def test_bootstrap_and_handoff_directories_use_platform_mode(self):
        expected = 0o777 if os.name == "nt" else 0o700
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            vault = root / "测试库"
            bootstrap = FirstRunBootstrap(documents_parent=root)
            request = BootstrapRequest(TASK_ID, "创建知识库", "obsidian", "测试库")
            preview = bootstrap.execute(request)
            real_mkdir = os.mkdir
            with mock.patch("os.mkdir", wraps=real_mkdir) as mkdir:
                result = bootstrap.execute(replace(request, confirmation=preview.confirmation))
                created = []
                handoff._ensure_directory(root / "host" / "runs", created)
            self.assertEqual(result.status, "created")
            saved = json.loads((vault / contract.MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8"))
            status = Stage2Router(ObsidianAdapter()).execute(RouterRequest(
                TASK_ID, "检查状态", "obsidian", str(vault), "客户", "测试库", "company"))
            self.assertEqual(status.client_id, saved["client_id"])
            self.assertEqual(status.status, "status")
            modes = {Path(call.args[0]): call.args[1] if len(call.args) > 1 else call.kwargs.get("mode", 0o777)
                     for call in mkdir.call_args_list}
            for path in (vault, vault / "01-来源索引", vault / "03-业务知识库", root / "host", root / "host" / "runs"):
                self.assertEqual(modes[path], expected, path)


if __name__ == "__main__":
    unittest.main()
