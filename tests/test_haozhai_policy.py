from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import (  # noqa: E402
    BINDING_SCHEMA,
    ROOT_KEYS,
    SOURCE_SCHEMA,
    Binding,
    MediaArtifact,
    SourceRecord,
)
from shared.haozhai_policy import (  # noqa: E402
    EvidenceItem,
    HaozhaiPolicyError,
    KnowledgeCard,
    SourceEvidence,
    build_knowledge_request,
    build_method_request,
    build_profile_request,
)
from shared.stage6_knowledge import Stage6Knowledge  # noqa: E402
from shared.stage7_method import Stage7Method  # noqa: E402
from shared.stage8_profile import Stage8Profile  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"


def binding() -> Binding:
    return Binding(
        BINDING_SCHEMA,
        "CLT-HAOZHAI",
        "沈牧",
        "豪宅知识库",
        "person",
        "obsidian",
        "C:/vault",
        {key: f"root:{key}" for key in ROOT_KEYS},
        TEMPLATE_VERSION,
    )


def evidence(role: str) -> SourceEvidence:
    page_texts = {
        1: "滨江项目采用大面积玻璃幕墙。适用于了解项目立面特点。",
        2: "开头先提出反常识问题。中段按场景推进。结尾邀请读者留言。沈牧负责豪宅项目研究。",
    }
    readable = "\n".join(page_texts.values())
    source_id = "SRC-" + "a" * 24
    media = tuple(
        MediaArtifact(
            f"{source_id}-PAGE-{page:03d}",
            source_id,
            page,
            "image",
            f"page-{page:03d}.png",
            hashlib.sha256(f"image-{page}".encode()).hexdigest(),
            ocr_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        for page, text in page_texts.items()
    )
    source = SourceRecord(
        SOURCE_SCHEMA,
        source_id,
        "CLT-HAOZHAI",
        "豪宅资料",
        role,
        "document",
        "资料.pdf",
        hashlib.sha256(b"pdf").hexdigest(),
        hashlib.sha256(readable.encode()).hexdigest(),
        "passed",
        "allowed",
        None,
        "registered",
        True,
        media_artifacts=media,
        page_count=2,
        visual_processing_status="ocr_completed",
    )
    return SourceEvidence(source, readable, page_texts)


class HaozhaiPolicyTests(unittest.TestCase):
    def test_03_is_literal_grounded_and_keeps_page_backlinks(self) -> None:
        source_evidence = evidence("business_knowledge")
        request = build_knowledge_request(
            TASK_ID,
            binding(),
            source_evidence,
            title="滨江项目立面知识卡",
            topic="项目与产品资料",
            category_id="WIKI03-CAT-02",
            summary=EvidenceItem("滨江项目采用大面积玻璃幕墙。", (1,)),
            cards=(KnowledgeCard("立面", (EvidenceItem("滨江项目采用大面积玻璃幕墙。", (1,)),)),),
            usage_notes=(EvidenceItem("适用于了解项目立面特点。", (1,)),),
        )

        self.assertEqual(request.evidence_pages, (1,))
        asset = Stage6Knowledge._asset(request)
        self.assertIn("第 1 页", asset.body)
        self.assertEqual(asset.metadata["policy_id"], "haozhai-v1")

        with self.assertRaisesRegex(HaozhaiPolicyError, "page_spec_not_grounded"):
            build_knowledge_request(
                TASK_ID,
                binding(),
                source_evidence,
                title="编造内容",
                topic="项目与产品资料",
                category_id="WIKI03-CAT-02",
                summary=EvidenceItem("来源没有这句话。", (1,)),
                cards=(KnowledgeCard("立面", (EvidenceItem("滨江项目采用大面积玻璃幕墙。", (1,)),)),),
                usage_notes=(EvidenceItem("适用于了解项目立面特点。", (1,)),),
            )

    def test_03_blocks_unqualified_high_risk_promise(self) -> None:
        source_evidence = evidence("business_knowledge")
        risky_text = source_evidence.readable_text + "保证百分之百升值。"
        risky = SourceEvidence(
            replace(source_evidence.source, readable_sha256=hashlib.sha256(risky_text.encode()).hexdigest()),
            risky_text,
            source_evidence.page_texts,
        )
        with self.assertRaisesRegex(HaozhaiPolicyError, "page_spec_high_risk"):
            build_knowledge_request(
                TASK_ID,
                binding(),
                risky,
                title="风险说法",
                topic="风险",
                category_id="WIKI03-CAT-05",
                summary=EvidenceItem("保证百分之百升值。"),
                cards=(KnowledgeCard("风险", (EvidenceItem("保证百分之百升值。"),)),),
                usage_notes=(EvidenceItem("保证百分之百升值。"),),
            )

    def test_04_requires_seven_grounded_sections_and_page_backlinks(self) -> None:
        source_evidence = evidence("reference_method")
        request = build_method_request(
            TASK_ID,
            binding(),
            source_evidence,
            title="反常识开头拆解",
            sections=(
                EvidenceItem("开头先提出反常识问题。", (2,)),
                EvidenceItem("开头先提出反常识问题。", (2,)),
                EvidenceItem("开头先提出反常识问题。", (2,)),
                EvidenceItem("中段按场景推进。", (2,)),
                EvidenceItem("中段按场景推进。", (2,)),
                EvidenceItem("结尾邀请读者留言。", (2,)),
                EvidenceItem("结尾邀请读者留言。", (2,)),
            ),
        )

        self.assertEqual(request.evidence_pages, (2,))
        self.assertIn("第 2 页", Stage7Method._asset(request).body)
        with self.assertRaisesRegex(HaozhaiPolicyError, "template_sections_required"):
            build_method_request(TASK_ID, binding(), source_evidence, title="不完整", sections=requested_sections(6))

    def test_04_policy_does_not_apply_generic_120_character_limit(self) -> None:
        long_text = "开头先提出反常识问题。" * 12
        source_evidence = evidence("reference_method")
        page_texts = dict(source_evidence.page_texts)
        page_texts[2] += long_text
        readable = "\n".join(page_texts.values())
        media = tuple(
            replace(item, ocr_text_sha256=hashlib.sha256(page_texts[item.page_number].encode()).hexdigest())
            for item in source_evidence.source.media_artifacts
        )
        expanded = SourceEvidence(
            replace(source_evidence.source, readable_sha256=hashlib.sha256(readable.encode()).hexdigest(), media_artifacts=media),
            readable,
            page_texts,
        )

        request = build_method_request(
            TASK_ID,
            binding(),
            expanded,
            title="长段拆解",
            sections=tuple(EvidenceItem(long_text, (2,)) for _ in range(7)),
        )

        self.assertEqual(len(request.policy_sections), 7)

    def test_05_requires_three_grounded_layers_and_reviewed_confirmed_facts(self) -> None:
        source_evidence = evidence("profile_material")
        confirmed = EvidenceItem("沈牧负责豪宅项目研究。", (2,))
        with self.assertRaisesRegex(HaozhaiPolicyError, "profile_confirmation_missing"):
            build_profile_request(
                TASK_ID,
                binding(),
                source_evidence,
                subject_name="沈牧",
                confirmed_facts=(confirmed,),
                operating_settings=(EvidenceItem("中段按场景推进。", (2,)),),
                candidate_materials=(EvidenceItem("结尾邀请读者留言。", (2,)),),
            )

        reviewed = SourceEvidence(
            source_evidence.source,
            source_evidence.readable_text,
            source_evidence.page_texts,
            reviewed_confirmed_sha256=frozenset({hashlib.sha256(confirmed.text.encode()).hexdigest()}),
        )
        request = build_profile_request(
            TASK_ID,
            binding(),
            reviewed,
            subject_name="沈牧",
            confirmed_facts=(confirmed,),
            operating_settings=(EvidenceItem("中段按场景推进。", (2,)),),
            candidate_materials=(EvidenceItem("结尾邀请读者留言。", (2,)),),
        )

        self.assertEqual(request.evidence_pages, (2,))
        self.assertIn("第 2 页", Stage8Profile._primary(request).body())

    def test_page_ocr_hash_must_match_media_manifest(self) -> None:
        source_evidence = evidence("business_knowledge")
        with self.assertRaisesRegex(HaozhaiPolicyError, "page_ocr_mismatch"):
            SourceEvidence(source_evidence.source, source_evidence.readable_text, {1: "被替换的 OCR", 2: source_evidence.page_texts[2]})


def requested_sections(count: int) -> tuple[EvidenceItem, ...]:
    return tuple(EvidenceItem("开头先提出反常识问题。", (2,)) for _ in range(count))


if __name__ == "__main__":
    unittest.main()
