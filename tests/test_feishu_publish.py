from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.approval import issue_publish_approval  # noqa: E402
from shared.contracts import (  # noqa: E402
    BINDING_SCHEMA,
    ROOT_KEYS,
    SOURCE_SCHEMA,
    AdapterResult,
    AssetPayload,
    BackendObjectRef,
    Binding,
    MediaArtifact,
    SourceRecord,
)
from shared.feishu_publish import FeishuPublishRequest, FeishuPublishWorkflow, PublishReceiptStore  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


class MemoryIO:
    def __init__(self) -> None:
        self.data = {}

    def read(self, path):
        return self.data.get(str(path))

    def write_atomic(self, path, payload):
        self.data[str(path)] = payload


def binding() -> Binding:
    return Binding(
        BINDING_SCHEMA,
        "CLT-FEISHU",
        "沈牧",
        "豪宅知识库",
        "person",
        "feishu",
        "https://feishu.cn/wiki/space/123",
        {key: f"root:{key}" for key in ROOT_KEYS},
        TEMPLATE_VERSION,
    )


def source() -> tuple[SourceRecord, dict[int, bytes]]:
    source_id = "SRC-" + "f" * 24
    payloads = {1: b"image-one", 2: b"image-two"}
    media = tuple(
        MediaArtifact(
            f"{source_id}-PAGE-{page:03d}", source_id, page, "image", f"page-{page:03d}.png",
            hashlib.sha256(payload).hexdigest(), ocr_text_sha256=hashlib.sha256(f"ocr-{page}".encode()).hexdigest(),
        )
        for page, payload in payloads.items()
    )
    item = SourceRecord(
        SOURCE_SCHEMA, source_id, "CLT-FEISHU", "来源", "business_knowledge", "document", "资料.pdf",
        hashlib.sha256(b"pdf").hexdigest(), hashlib.sha256(b"readable").hexdigest(), "passed", "allowed", None,
        "registered", True, media_artifacts=media, page_count=2, visual_processing_status="ocr_completed",
    )
    return item, payloads


def request(approval: dict | None = None) -> FeishuPublishRequest:
    item, payloads = source()
    draft = "# 正式知识页\n\n正文\n".encode("utf-8")
    asset = AssetPayload(
        "KNO-1234567890abcdef", "正式知识页", draft.decode(), item.source_id, item.source_role,
        {"evidence_pages": [1, 2], "policy_id": "haozhai-v1"},
    )
    valid = approval or issue_publish_approval(
        item.source_id, item.client_id, "03", draft, item.media_artifacts,
        classification_confirmed=True, draft_confirmed=True,
    )
    return FeishuPublishRequest(binding(), item, asset, "03", draft, b"readable", payloads, valid)


@dataclass
class FakePublishBackend:
    fail_asset_once: bool = False

    def __post_init__(self) -> None:
        self.calls = []

    def doctor(self, _binding):
        self.calls.append("doctor")
        return AdapterResult.ok(checked=("auth", "scopes"))

    def publish_source(self, _binding, _source, _readable, _media):
        self.calls.append("source")
        ref = BackendObjectRef("source-doc", "source_readable", "feishu://01/source", "1")
        return AdapterResult.ok(ref, checked=("source_images", "readback"), metadata={"document_url": "https://example.feishu.cn/docx/source"})

    def publish_asset(self, _binding, _source, _asset, _destination, _media):
        self.calls.append("asset")
        if self.fail_asset_once:
            self.fail_asset_once = False
            return AdapterResult.failed("write_failed", "temporary")
        ref = BackendObjectRef("asset-doc", "knowledge_asset", "feishu://03/asset", "1")
        return AdapterResult.ok(ref, checked=("asset_images", "readback"), metadata={"document_url": "https://example.feishu.cn/docx/asset"})

    def read_back(self, _binding, refs):
        self.calls.append("readback")
        return AdapterResult.ok(*refs, checked=("content", "images", "stable_refs"))


class FeishuPublishWorkflowTests(unittest.TestCase):
    def test_invalid_approval_is_zero_write(self) -> None:
        backend = FakePublishBackend()
        store = PublishReceiptStore(Path("C:/runtime/receipts"), io=MemoryIO())
        invalid = request()
        bad = FeishuPublishRequest(
            invalid.binding, invalid.source, invalid.asset, invalid.destination, invalid.draft,
            invalid.readable_payload, invalid.media_payloads, {**invalid.approval, "client_id": "CLT-WRONG"},
        )

        with self.assertRaisesRegex(ValueError, "publish_approval_invalid"):
            FeishuPublishWorkflow(backend, store).publish(bad)
        self.assertEqual(backend.calls, [])

    def test_published_receipt_contains_two_links_and_readback(self) -> None:
        backend = FakePublishBackend()
        store = PublishReceiptStore(Path("C:/runtime/receipts"), io=MemoryIO())

        result = FeishuPublishWorkflow(backend, store).publish(request())

        self.assertEqual(result.record["status"], "published")
        self.assertEqual(result.record["source_document_url"], "https://example.feishu.cn/docx/source")
        self.assertEqual(result.record["knowledge_document_url"], "https://example.feishu.cn/docx/asset")
        self.assertEqual(result.record["readback"]["status"], "verified")
        self.assertEqual(backend.calls, ["doctor", "source", "asset", "readback"])

    def test_retry_resumes_after_source_without_duplicate_source_write(self) -> None:
        backend = FakePublishBackend(fail_asset_once=True)
        store = PublishReceiptStore(Path("C:/runtime/receipts"), io=MemoryIO())
        workflow = FeishuPublishWorkflow(backend, store)

        first = workflow.publish(request())
        second = workflow.publish(request())

        self.assertEqual(first.record["status"], "pending")
        self.assertEqual(first.record["last_error"], "write_failed")
        self.assertEqual(second.record["status"], "published")
        self.assertEqual(backend.calls.count("source"), 1)
        self.assertEqual(backend.calls.count("asset"), 2)
        self.assertEqual(second.record["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
