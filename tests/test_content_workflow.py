from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.approval import ApprovalStore  # noqa: E402
from shared.content_workflow import ContentWorkflow  # noqa: E402
from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, SOURCE_SCHEMA, Binding, MediaArtifact, SourceRecord  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


class MemoryIO:
    def __init__(self) -> None:
        self.data = {}

    def read(self, path):
        return self.data.get(str(path))

    def write_atomic(self, path, payload):
        self.data[str(path)] = payload


def binding() -> Binding:
    return Binding(BINDING_SCHEMA, "CLT-123", "客户", "知识库", "company", "obsidian", "C:/vault", {key: f"root:{key}" for key in ROOT_KEYS}, TEMPLATE_VERSION)


def source() -> SourceRecord:
    source_id = "SRC-" + "a" * 24
    media = MediaArtifact(f"{source_id}-PAGE-001", source_id, 1, "image", "page-001.png", hashlib.sha256(b"image").hexdigest())
    return SourceRecord(SOURCE_SCHEMA, source_id, "CLT-123", "楼盘资料", "business_knowledge", "document", "楼盘.pdf", "b" * 64, "c" * 64, "passed", "allowed", None, "registered", True, media_artifacts=(media,), page_count=1, visual_processing_status="ocr_completed")


class ContentWorkflowTests(unittest.TestCase):
    def test_router_workflow_persists_two_content_confirmations(self) -> None:
        store = ApprovalStore(Path("C:/runtime/approvals"), io=MemoryIO())
        workflow = ContentWorkflow(store)
        item = source()

        started = workflow.begin(binding(), item, "03", "正文记录客户自己的楼盘事实")
        classified = workflow.confirm_destination(item.source_id, "03", expected_sha256=started.sha256)
        draft = "# 楼盘知识页\n".encode("utf-8")
        drafted = workflow.present_draft(item.source_id, draft, expected_sha256=classified.sha256)
        approved = workflow.confirm_publish(item, draft, expected_sha256=drafted.sha256)

        self.assertEqual(store.load(item.source_id)["type"], "zsk_publish_approval_v1")
        self.assertEqual(approved.record["classification_confirmation"]["destination"], "03")
        self.assertTrue(approved.record["draft_confirmation"]["confirmed"])

    def test_router_workflow_does_not_accept_draft_before_classification(self) -> None:
        store = ApprovalStore(Path("C:/runtime/approvals"), io=MemoryIO())
        workflow = ContentWorkflow(store)
        item = source()
        started = workflow.begin(binding(), item, "03", "正文依据")
        with self.assertRaisesRegex(ValueError, "classification_confirmation_required"):
            workflow.present_draft(item.source_id, b"draft", expected_sha256=started.sha256)


if __name__ == "__main__":
    unittest.main()
