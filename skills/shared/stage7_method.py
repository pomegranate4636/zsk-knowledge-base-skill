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

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID")
        if self.source.client_id != self.binding.client_id:
            raise ValueError("source must belong to the active binding")
        for field_name in ("title", "topic", "opening_mechanism", "progression_mechanism", "expression_mechanism", "closing_mechanism", "transferable_method"):
            _mechanism(getattr(self, field_name), field_name)


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
        body = f"# {request.title.strip()}\n\n## 主题\n\n{request.topic.strip()}\n\n## 开头机制\n\n{request.opening_mechanism.strip()}\n\n## 中间推进\n\n{request.progression_mechanism.strip()}\n\n## 表达机制\n\n{request.expression_mechanism.strip()}\n\n## 结尾行动\n\n{request.closing_mechanism.strip()}\n\n## 可迁移方法\n\n{request.transferable_method.strip()}\n\n## 不可照搬\n\n- 身份、案例、数据、承诺和长段原文不进入方法卡。\n\n## 来源\n\n- `{request.source.source_id}`\n"
        return AssetPayload(asset_id, request.title.strip(), body, request.source.source_id, request.source.source_role, {"topic": request.topic.strip(), "asset_root": "04"})
