"""阶段 7：把已登记的对标来源沉淀为仅含表达机制的 04 方法卡。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from .adapter import KnowledgeBaseAdapter
from .contracts import AssetPayload, Binding, SourceRecord, TASK_ID


_UNSAFE = (
    re.compile(r"(?:案例|原文|逐字|引用)"),
    re.compile(r"(?:我的|某位?|该)(?:客户|朋友|公司|品牌|账号|机构)"),
    re.compile(r"(?:保证|承诺|稳赚|收益)"),
    re.compile(r"\d+(?:[.,]\d+)?(?:%|万|亿|元|人|次|年|月|日)"),
)
_HAOZHAI_HEADINGS = ("标题概要", "选题成立", "开头", "推进", "故事与表达", "CTA", "参考方式")


def _mechanism(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value or len(value.strip()) > 120:
        raise ValueError(f"{field_name} must be one short, non-empty mechanism")
    text = value.strip()
    if any(pattern.search(text) for pattern in _UNSAFE):
        raise ValueError(f"{field_name} contains identity, case, data, promise or quoted-source content")
    return text


@dataclass(frozen=True)
class MethodRequest:
    task_id: str
    binding: Binding
    source: SourceRecord
    title: str
    topic: str
    opening_mechanism: str
    progression_mechanism: str
    expression_mechanism: str
    closing_mechanism: str
    transferable_method: str
    policy_sections: tuple[tuple[str, str], ...] = ()
    evidence_pages: tuple[int, ...] = ()
    policy_id: str = ""
    policy_receipt: str = ""

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID")
        if self.source.client_id != self.binding.client_id:
            raise ValueError("source must belong to the active binding")
        _mechanism(self.title, "title")
        if self.policy_id:
            if self.policy_id != "haozhai-v1" or not re.fullmatch(r"[0-9a-f]{64}", self.policy_receipt):
                raise ValueError("policy receipt is invalid")
            if tuple(heading for heading, _content in self.policy_sections) != _HAOZHAI_HEADINGS:
                raise ValueError("policy sections are invalid")
            for heading, content in self.policy_sections:
                if not isinstance(content, str) or not content.strip() or len(content.strip()) > 4000:
                    raise ValueError(f"{heading} must be a non-empty grounded section")
                if any(pattern.search(content.strip()) for pattern in _UNSAFE):
                    raise ValueError(f"{heading} contains identity, case, data, promise or quoted-source content")
        else:
            for field_name in ("topic", "opening_mechanism", "progression_mechanism", "expression_mechanism", "closing_mechanism", "transferable_method"):
                _mechanism(getattr(self, field_name), field_name)
        if any(not isinstance(page, int) or page < 1 for page in self.evidence_pages):
            raise ValueError("evidence_pages must contain positive page numbers")


@dataclass(frozen=True)
class MethodResponse:
    status: str
    code: str | None
    asset: AssetPayload | None
    evidence: dict[str, Any]


class Stage7Method:
    """只写 04 表达机制；来源、内容或回读不满足时立即停止。"""

    def __init__(self, adapter: KnowledgeBaseAdapter) -> None:
        self.adapter = adapter

    def execute(self, request: MethodRequest) -> MethodResponse:
        evidence = {"schema_version": "zsk-stage7-evidence-v1", "task_id": request.task_id, "source_id": request.source.source_id, "events": [], "model_call_count": 0, "downstream_asset_call_count": 0}
        for action, call in (("doctor", self.adapter.doctor), ("resolve_binding", lambda: self.adapter.resolve_binding(request.binding)), ("inspect_structure", lambda: self.adapter.inspect_structure(request.binding))):
            result = call()
            evidence["events"].append({"action": action, "status": result.status, "code": result.code})
            if result.status not in {"ok", "reused"} or action == "inspect_structure" and result.status != "reused":
                return MethodResponse("exception", result.code or "structure_conflict", None, evidence)
        code = self._source_code(request.source)
        if code:
            evidence["events"].append({"action": "source_gate", "status": "blocked", "code": code})
            return MethodResponse("exception", code, None, evidence)
        asset = self._asset(request)
        result = self.adapter.write_method_asset(request.binding, asset)
        evidence["events"].append({"action": "write_method_asset", "status": result.status, "code": result.code})
        if result.status not in {"ok", "reused"}:
            return MethodResponse("exception", result.code or "write_failed", None, evidence)
        evidence["downstream_asset_call_count"] = 1
        readback = self.adapter.read_back(request.binding, result.object_refs)
        evidence["events"].append({"action": "read_back", "status": readback.status, "code": readback.code})
        if readback.status not in {"ok", "reused"}:
            return MethodResponse("exception", readback.code or "readback_failed", None, evidence)
        evidence.update({"status": "reused" if result.status == "reused" else "registered", "asset_id": asset.asset_id})
        return MethodResponse(evidence["status"], None, asset, evidence)

    @staticmethod
    def _source_code(source: SourceRecord) -> str | None:
        if source.source_role != "reference_method":
            return "routing_ambiguous"
        if source.status not in {"registered", "reused"}:
            return "ownership_unknown"
        if source.permission_status != "allowed":
            return "permission_denied"
        if source.privacy_status not in {"passed", "redacted"}:
            return "privacy_blocked"
        return None

    @staticmethod
    def _asset(request: MethodRequest) -> AssetPayload:
        fields = (request.source.source_id, request.title.strip(), request.topic.strip(), request.opening_mechanism.strip(), request.progression_mechanism.strip(), request.expression_mechanism.strip(), request.closing_mechanism.strip(), request.transferable_method.strip())
        asset_id = "MET-" + hashlib.sha256("\n".join(fields).encode("utf-8")).hexdigest()[:16]
        page_lines = "\n".join(f"- `{request.source.source_id}` · 第 {page} 页 · `page-{page:03d}.png`" for page in request.evidence_pages)
        source_lines = page_lines or f"- `{request.source.source_id}`"
        if request.policy_sections:
            sections = "\n\n".join(f"## {heading}\n\n{content}" for heading, content in request.policy_sections)
            body = f"# {request.title.strip()}\n\n{sections}\n\n## 来源\n\n{source_lines}\n"
        else:
            body = f"# {request.title.strip()}\n\n## 主题\n\n{request.topic.strip()}\n\n## 开头机制\n\n{request.opening_mechanism.strip()}\n\n## 中间推进\n\n{request.progression_mechanism.strip()}\n\n## 表达机制\n\n{request.expression_mechanism.strip()}\n\n## 结尾行动\n\n{request.closing_mechanism.strip()}\n\n## 可迁移方法\n\n{request.transferable_method.strip()}\n\n## 不可照搬\n\n- 身份、案例、数据、承诺和长段原文不进入方法卡。\n\n## 来源\n\n{source_lines}\n"
        metadata = {"topic": request.topic.strip(), "asset_root": "04"}
        if request.policy_id:
            metadata.update({"policy_id": request.policy_id, "policy_receipt": request.policy_receipt, "evidence_pages": list(request.evidence_pages)})
        return AssetPayload(asset_id, request.title.strip(), body, request.source.source_id, request.source.source_role, metadata)
