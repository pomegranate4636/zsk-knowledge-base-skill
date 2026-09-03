from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import (  # noqa: E402
    BINDING_SCHEMA,
    SOURCE_SCHEMA,
    Binding,
    KnowledgeFact,
    PageArtifact,
    PageTextEvidence,
    SourceRecord,
)
from shared.fake_adapter import FakeAdapter  # noqa: E402
from shared.stage6_knowledge import KnowledgeRequest, Stage6Knowledge  # noqa: E402


TASK_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "SRC-" + hashlib.sha256(b"deck").hexdigest()[:24]
PAGE_HASH = hashlib.sha256(b"page").hexdigest()


def binding() -> Binding:
    roots = {key: f"root-{key}" for key in ("01", "02", "03", "04", "05", "06", "07", "AGENTS", "README")}
    return Binding(BINDING_SCHEMA, "CLT-1234567890ABCD", "客户", "测试库", "company", "fake", "fake://kb", roots, "v1")


def source() -> SourceRecord:
    artifact = PageArtifact(f"{SOURCE_ID}-PAGE-001", SOURCE_ID, 1, "page-001.png", PAGE_HASH, 1600, 900)
    text = PageTextEvidence.create(
        SOURCE_ID,
        1,
        PAGE_HASH,
        native_text="原文事实",
        ocr_text="",
        verbatim_text="原文事实",
        text_source="native",
        confidence=1.0,
        review_status="verified_native",
    )
    return SourceRecord(
        SOURCE_SCHEMA,
        SOURCE_ID,
        "CLT-1234567890ABCD",
        "PPT资料",
        "business_knowledge",
        "presentation",
        "资料.pptx",
        hashlib.sha256(b"deck").hexdigest(),
        hashlib.sha256(b"readable").hexdigest(),
        "passed",
        "allowed",
        None,
        "registered",
        True,
        page_evidence_mode="required",
        page_count=1,
        page_artifacts=(artifact,),
        page_text_evidence=(text,),
        display_name="PPT资料",
    )


class KnowledgePageEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = binding()
        self.adapter = FakeAdapter()
        self.adapter.resolve_binding(self.binding)
        self.adapter.create_skeleton(self.binding)
        src = source()
        self.adapter.store_original(self.binding, src, b"deck")
        self.adapter.store_readable(self.binding, src, b"readable")

    def test_page_evidence_source_rejects_unbound_fact_text(self) -> None:
        response = Stage6Knowledge(self.adapter).execute(
            KnowledgeRequest(TASK_ID, self.binding, source(), "知识卡", "主题", "概括事实")
        )
        self.assertEqual((response.status, response.code), ("exception", "evidence_incomplete"))

    def test_fact_must_match_page_verbatim_and_hash(self) -> None:
        response = Stage6Knowledge(self.adapter).execute(
            KnowledgeRequest(
                TASK_ID,
                self.binding,
                source(),
                "知识卡",
                "主题",
                "概括事实",
                fact_evidence=(KnowledgeFact("概括事实", 1, "错误原文", "0" * 64),),
            )
        )
        self.assertEqual((response.status, response.code), ("exception", "evidence_incomplete"))

    def test_fact_quote_must_be_an_exact_page_excerpt(self) -> None:
        text = source().page_text_evidence[0]
        response = Stage6Knowledge(self.adapter).execute(
            KnowledgeRequest(
                TASK_ID,
                self.binding,
                source(),
                "知识卡",
                "主题",
                "概括事实",
                fact_evidence=(KnowledgeFact("概括事实", 1, "不存在的原文", text.evidence_sha256),),
            )
        )
        self.assertEqual((response.status, response.code), ("exception", "evidence_incomplete"))

    def test_valid_fact_writes_page_original_and_hash(self) -> None:
        text = source().page_text_evidence[0]
        response = Stage6Knowledge(self.adapter).execute(
            KnowledgeRequest(
                TASK_ID,
                self.binding,
                source(),
                "知识卡",
                "主题",
                "概括事实",
                fact_evidence=(KnowledgeFact("概括事实", 1, "原文事实", text.evidence_sha256),),
            )
        )
        self.assertEqual((response.status, response.code), ("registered", None))
        assert response.asset is not None
        self.assertIn(f"asset_id: {response.asset.asset_id}\n", response.asset.body)
        self.assertIn("type: business_knowledge_asset\n", response.asset.body)
        self.assertIn("status: confirmed\n", response.asset.body)
        self.assertIn("applicable_workflows:\n  - content-koubo-slim\n  - content-gzh-slim\n", response.asset.body)
        self.assertIn("第 1 页", response.asset.body)
        self.assertIn("原文事实", response.asset.body)
        self.assertIn(text.evidence_sha256, response.asset.body)
        self.assertEqual(response.asset.metadata["status"], "confirmed")
        self.assertEqual(len(response.asset.metadata["fact_evidence"]), 1)


if __name__ == "__main__":
    unittest.main()
