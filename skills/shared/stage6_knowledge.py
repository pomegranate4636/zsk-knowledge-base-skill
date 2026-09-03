"""阶段 6：把已登记的业务来源写成可追溯的 03 知识卡。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .adapter import KnowledgeBaseAdapter
from .contracts import AssetPayload, Binding, KnowledgeFact, SourceRecord, TASK_ID


@dataclass(frozen=True)
class KnowledgeRequest:
    task_id: str
    binding: Binding
    source: SourceRecord
    title: str
    topic: str
    facts: str
    applicability: str = ""
    cautions: str = ""
    fact_evidence: tuple[KnowledgeFact, ...] = ()

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID")
        if self.source.client_id != self.binding.client_id:
            raise ValueError("source must belong to the active binding")
        if not all(isinstance(value, str) and value.strip() for value in (self.title, self.topic, self.facts)):
            raise ValueError("title, topic and facts are required")
        if any(not isinstance(item, KnowledgeFact) for item in self.fact_evidence):
            raise ValueError("fact_evidence must contain KnowledgeFact values")


@dataclass(frozen=True)
class KnowledgeResponse:
    status: str
    code: str | None
    asset: AssetPayload | None
    evidence: dict[str, Any]


class Stage6Knowledge:
    """只接收已判断为业务知识的事实卡；不做评分、不写 04/05。"""

    def __init__(self, adapter: KnowledgeBaseAdapter) -> None:
        self.adapter = adapter

    def execute(self, request: KnowledgeRequest) -> KnowledgeResponse:
        evidence = {"schema_version": "zsk-stage6-evidence-v1", "task_id": request.task_id, "source_id": request.source.source_id, "events": [], "model_call_count": 0, "downstream_asset_call_count": 0}
        for action, call in (("doctor", self.adapter.doctor), ("resolve_binding", lambda: self.adapter.resolve_binding(request.binding)), ("inspect_structure", lambda: self.adapter.inspect_structure(request.binding))):
            result = call()
            evidence["events"].append({"action": action, "status": result.status, "code": result.code})
            if result.status not in {"ok", "reused"} or action == "inspect_structure" and result.status != "reused":
                return KnowledgeResponse("exception", result.code or "structure_conflict", None, evidence)
        code = self._source_code(request)
        if code:
            evidence["events"].append({"action": "source_gate", "status": "blocked", "code": code})
            return KnowledgeResponse("exception", code, None, evidence)
        asset = self._asset(request)
        result = self.adapter.write_knowledge_asset(request.binding, asset)
        evidence["events"].append({"action": "write_knowledge_asset", "status": result.status, "code": result.code})
        if result.status not in {"ok", "reused"}:
            return KnowledgeResponse("exception", result.code or "write_failed", None, evidence)
        evidence["status"] = "reused" if result.status == "reused" else "registered"
        evidence["asset_id"] = asset.asset_id
        evidence["downstream_asset_call_count"] = 1
        return KnowledgeResponse(evidence["status"], None, asset, evidence)

    @staticmethod
    def _source_code(request: KnowledgeRequest) -> str | None:
        source = request.source
        if source.source_role not in {"business_knowledge", "mixed", "unknown"}:
            return "routing_ambiguous"
        if source.status not in {"registered", "reused"}:
            return "ownership_unknown"
        if source.permission_status != "allowed":
            return "permission_denied"
        if source.privacy_status not in {"passed", "redacted"}:
            return "privacy_blocked"
        if source.page_evidence_mode == "required":
            if not source.page_text_evidence or not request.fact_evidence:
                return "evidence_incomplete"
            expected_facts = "\n".join(item.fact.strip() for item in request.fact_evidence)
            if request.facts.strip() != expected_facts:
                return "evidence_incomplete"
            evidence_by_page = {item.page_number: item for item in source.page_text_evidence}
            for fact in request.fact_evidence:
                page = evidence_by_page.get(fact.page_number)
                if (
                    page is None
                    or page.review_status == "review_required"
                    or fact.verbatim_text.strip() not in page.verbatim_text
                    or fact.evidence_sha256 != page.evidence_sha256
                ):
                    return "evidence_incomplete"
        return None

    @staticmethod
    def _asset(request: KnowledgeRequest) -> AssetPayload:
        material = "\n".join((request.source.source_id, request.title.strip(), request.topic.strip(), request.facts.strip()))
        asset_id = "KNO-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        applicability = request.applicability.strip() or "仅在来源所述场景中使用。"
        cautions = request.cautions.strip() or "不得补充来源未说明的事实或承诺。"
        topic = json.dumps(request.topic.strip(), ensure_ascii=False)
        scope = json.dumps(applicability, ensure_ascii=False)
        if request.fact_evidence:
            facts = "\n\n".join(
                f"### {item.fact.strip()}\n\n- 页码：第 {item.page_number} 页\n- 原文：{item.verbatim_text}\n- 证据哈希：`{item.evidence_sha256}`"
                for item in request.fact_evidence
            )
        else:
            facts = request.facts.strip()
        body = (
            "---\n"
            f"asset_id: {asset_id}\n"
            "type: business_knowledge_asset\n"
            "status: confirmed\n"
            "keywords:\n"
            f"  - {topic}\n"
            "applicable_workflows:\n"
            "  - content-koubo-slim\n"
            "  - content-gzh-slim\n"
            "applicability:\n"
            f"  - {scope}\n"
            f"source_id: \"{request.source.source_id}\"\n"
            "---\n\n"
            f"# {request.title.strip()}\n\n## 主题\n\n{request.topic.strip()}\n\n"
            f"## 核心知识\n\n{facts}\n\n## 适用范围\n\n{applicability}\n\n"
            f"## 使用边界\n\n{cautions}\n\n## 来源\n\n- {request.source.source_title}\n"
        )
        return AssetPayload(
            asset_id,
            request.title.strip(),
            body,
            request.source.source_id,
            "business_knowledge",
            {
                "topic": request.topic.strip(),
                "status": "confirmed",
                "applicable_workflows": ("content-koubo-slim", "content-gzh-slim"),
                "fact_evidence": [item.as_dict() for item in request.fact_evidence],
            },
        )
