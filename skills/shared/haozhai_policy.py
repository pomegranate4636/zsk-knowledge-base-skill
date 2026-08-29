"""豪宅知识库的 03/04/05 内容合同。

本模块位于通用 ZSK 的策略层：核心阶段保持后端中立，Router 为豪宅项目选用
``haozhai-v1`` 时，所有正式内容都必须来自已回读的来源正文，并能回链到
PDF/PPT 的逐页图片与 OCR 证据。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping, Sequence

from .contracts import Binding, SourceRecord
from .stage6_knowledge import KnowledgeRequest
from .stage7_method import MethodRequest
from .stage8_profile import ProfileLayers, ProfileRequest


POLICY_ID = "haozhai-v1"
KNOWLEDGE_CATEGORIES = frozenset(
    {
        "WIKI03-CAT-01",
        "WIKI03-CAT-02",
        "WIKI03-CAT-03",
        "WIKI03-CAT-04",
        "WIKI03-CAT-05",
        "WIKI03-CAT-06",
    }
)
METHOD_HEADINGS = ("标题概要", "选题成立", "开头", "推进", "故事与表达", "CTA", "参考方式")

_PROMISE_TERMS = re.compile(r"保证|承诺|一定|必然|确保|零风险|无风险|保本")
_RETURN_TERMS = re.compile(r"上涨|升值|收益|回报|赚钱|获利|翻倍|稳赚|涨幅|年化")
_ABSOLUTE_RISK_TERMS = re.compile(r"绝对稳赚|稳赚不赔|保本保收益|百分之百升值")
_PROHIBITION_PREFIX = re.compile(r"^\s*(?:\d+[.、]\s*)?(?:严禁|禁止|不得|不能|切勿|勿用|避免使用|不(?:作|做)?承诺|不保证|不能保证|不得保证|无法承诺|无法保证)")
_DIRECT_IDENTIFIER = re.compile(r"(?:1[3-9]\d{9}|(?:微信|手机号|电话|身份证|邮箱)\s*[:：])", re.I)


class HaozhaiPolicyError(ValueError):
    """稳定错误码；Router 可直接据此进入阻断或人工复核。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


@dataclass(frozen=True)
class EvidenceItem:
    text: str
    pages: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise HaozhaiPolicyError("evidence_item_invalid")
        if not isinstance(self.pages, tuple) or any(not isinstance(page, int) or page < 1 for page in self.pages):
            raise HaozhaiPolicyError("page_reference_invalid")
        if len(self.pages) != len(set(self.pages)):
            raise HaozhaiPolicyError("page_reference_invalid")


@dataclass(frozen=True)
class KnowledgeCard:
    title: str
    items: tuple[EvidenceItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip() or not self.items:
            raise HaozhaiPolicyError("page_spec_incomplete")


@dataclass(frozen=True)
class SourceEvidence:
    """Stage 5 来源回读及逐页 OCR；哈希必须与 SourceRecord 完全一致。"""

    source: SourceRecord
    readable_text: str
    page_texts: Mapping[int, str]
    reviewed_confirmed_sha256: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.readable_text, str) or not self.readable_text.strip():
            raise HaozhaiPolicyError("source_unreadable")
        digest = hashlib.sha256(self.readable_text.encode("utf-8")).hexdigest()
        if digest != self.source.readable_sha256:
            raise HaozhaiPolicyError("source_readback_mismatch")
        artifacts = {item.page_number: item for item in self.source.media_artifacts}
        if any(not isinstance(page, int) or not isinstance(text, str) or not text.strip() for page, text in self.page_texts.items()):
            raise HaozhaiPolicyError("page_ocr_mismatch")
        if self.source.media_artifacts and set(self.page_texts) != set(artifacts):
            raise HaozhaiPolicyError("page_ocr_mismatch")
        for page, text in self.page_texts.items():
            artifact = artifacts.get(page)
            actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if artifact is None or artifact.ocr_text_sha256 != actual:
                raise HaozhaiPolicyError("page_ocr_mismatch")
        if self.source.media_artifacts and self.source.visual_processing_status != "ocr_completed":
            raise HaozhaiPolicyError("visual_evidence_incomplete")

    def validate_item(self, item: EvidenceItem, *, code: str) -> None:
        text = item.text.strip()
        if text not in self.readable_text:
            raise HaozhaiPolicyError(code)
        if self.source.media_artifacts and not item.pages:
            raise HaozhaiPolicyError("page_reference_required")
        for page in item.pages:
            page_text = self.page_texts.get(page)
            if page_text is None or text not in page_text:
                raise HaozhaiPolicyError("page_reference_not_grounded")


def _policy_receipt(destination: str, evidence: SourceEvidence, payload: object) -> str:
    canonical = json.dumps(
        {"policy_id": POLICY_ID, "destination": destination, "source_id": evidence.source.source_id, "readable_sha256": evidence.source.readable_sha256, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _pages(items: Sequence[EvidenceItem]) -> tuple[int, ...]:
    return tuple(sorted({page for item in items for page in item.pages}))


def _high_risk(text: str) -> bool:
    for segment in re.split(r"[。；;！？!，,、：:\n]+", text):
        risky = bool(_ABSOLUTE_RISK_TERMS.search(segment) or (_PROMISE_TERMS.search(segment) and _RETURN_TERMS.search(segment)))
        if risky and not _PROHIBITION_PREFIX.match(segment):
            return True
    return False


def _privacy_gate(values: Sequence[str], code: str) -> None:
    if any(_DIRECT_IDENTIFIER.search(value) for value in values):
        raise HaozhaiPolicyError(code)


def build_knowledge_request(
    task_id: str,
    binding: Binding,
    evidence: SourceEvidence,
    *,
    title: str,
    topic: str,
    category_id: str,
    summary: EvidenceItem,
    cards: tuple[KnowledgeCard, ...],
    usage_notes: tuple[EvidenceItem, ...],
) -> KnowledgeRequest:
    if category_id not in KNOWLEDGE_CATEGORIES:
        raise HaozhaiPolicyError("classification_unconfirmed")
    if not title.strip() or not topic.strip() or not cards or not usage_notes:
        raise HaozhaiPolicyError("page_spec_incomplete")
    items = (summary, *(item for card in cards for item in card.items), *usage_notes)
    values = (title, topic, *(item.text for item in items))
    _privacy_gate(values, "page_spec_privacy_blocked")
    if any(_high_risk(value) for value in values):
        raise HaozhaiPolicyError("page_spec_high_risk")
    for item in items:
        evidence.validate_item(item, code="page_spec_not_grounded")
    card_markdown = "\n\n".join(
        f"### 知识卡 {index}｜{card.title.strip()}\n\n" + "\n".join(item.text.strip() for item in card.items)
        for index, card in enumerate(cards, 1)
    )
    payload = {
        "category_id": category_id,
        "summary": summary.text.strip(),
        "cards": [{"title": card.title.strip(), "items": [item.text.strip() for item in card.items]} for card in cards],
        "usage_notes": [item.text.strip() for item in usage_notes],
        "pages": _pages(items),
    }
    return KnowledgeRequest(
        task_id,
        binding,
        evidence.source,
        title,
        topic,
        card_markdown,
        "\n".join(item.text.strip() for item in usage_notes),
        "不得补充来源未说明的事实或承诺。",
        category_id=category_id,
        policy_summary=summary.text.strip(),
        evidence_pages=_pages(items),
        policy_id=POLICY_ID,
        policy_receipt=_policy_receipt("03", evidence, payload),
    )


def build_method_request(
    task_id: str,
    binding: Binding,
    evidence: SourceEvidence,
    *,
    title: str,
    sections: tuple[EvidenceItem, ...],
) -> MethodRequest:
    if len(sections) != len(METHOD_HEADINGS):
        raise HaozhaiPolicyError("template_sections_required")
    values = (title, *(item.text for item in sections))
    _privacy_gate(values, "benchmark_privacy_blocked")
    for item in sections:
        evidence.validate_item(item, code="benchmark_spec_not_grounded")
    payload = {heading: item.text.strip() for heading, item in zip(METHOD_HEADINGS, sections)}
    payload["pages"] = _pages(sections)
    return MethodRequest(
        task_id,
        binding,
        evidence.source,
        title,
        sections[0].text,
        sections[2].text,
        sections[3].text,
        sections[4].text,
        sections[5].text,
        sections[6].text,
        policy_sections=tuple((heading, item.text.strip()) for heading, item in zip(METHOD_HEADINGS, sections)),
        evidence_pages=_pages(sections),
        policy_id=POLICY_ID,
        policy_receipt=_policy_receipt("04", evidence, payload),
    )


def build_profile_request(
    task_id: str,
    binding: Binding,
    evidence: SourceEvidence,
    *,
    subject_name: str,
    confirmed_facts: tuple[EvidenceItem, ...],
    operating_settings: tuple[EvidenceItem, ...],
    candidate_materials: tuple[EvidenceItem, ...],
) -> ProfileRequest:
    if subject_name.strip() != binding.client_name.strip():
        raise HaozhaiPolicyError("profile_identity_mismatch")
    if not confirmed_facts or not operating_settings or not candidate_materials:
        raise HaozhaiPolicyError("profile_layers_invalid")
    groups = (confirmed_facts, operating_settings, candidate_materials)
    items = tuple(item for group in groups for item in group)
    _privacy_gate((subject_name, *(item.text for item in items)), "profile_privacy_blocked")
    for item in items:
        evidence.validate_item(item, code="profile_item_not_grounded")
    for item in confirmed_facts:
        digest = hashlib.sha256(item.text.strip().encode("utf-8")).hexdigest()
        if digest not in evidence.reviewed_confirmed_sha256:
            raise HaozhaiPolicyError("profile_confirmation_missing")
    layers = ProfileLayers(
        tuple(item.text.strip() for item in confirmed_facts),
        tuple(item.text.strip() for item in operating_settings),
        tuple(item.text.strip() for item in candidate_materials),
    )
    payload = {"subject_name": subject_name.strip(), "layers": layers.as_dict(), "pages": _pages(items)}
    return ProfileRequest(
        task_id,
        binding,
        evidence.source,
        subject_name,
        layers,
        evidence_pages=_pages(items),
        policy_id=POLICY_ID,
        policy_receipt=_policy_receipt("05", evidence, payload),
    )
