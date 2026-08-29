"""建库骨架兼容 Router；公开运行入口由 zsk_entry.ZskEntry 提供。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .adapter import KnowledgeBaseAdapter
from .contracts import AdapterResult, BINDING_SCHEMA, Binding, ROOT_KEYS, SUBJECT_TYPES, TASK_ID, locator_has_credential
from .evidence import RunEvidence
from .stage5_intake import IntakeRequest, IntakeResponse, Stage5Intake
from .templates import TEMPLATE_VERSION, template_preview


PHASE_ID = "ZSK-P2"
REAL_FEISHU_PHASE_ID = "ZSK-P3"
REAL_OBSIDIAN_PHASE_ID = "ZSK-P4"
FAKE_RUN_ID = "ZSK-S2-FAKE-20260825-001"
EXECUTION_MODEL = "gpt-5.6-terra"
REASONING_EFFORT = "xhigh"
SUPPORTED_BACKENDS = frozenset({"feishu", "obsidian"})
_FEISHU_NODE = re.compile(r"^[A-Za-z0-9_-]+$")
_FEISHU_SPACE = re.compile(r"^[0-9]+$")


@dataclass(frozen=True)
class ConfirmationReceipt:
    token: str
    preview_sha256: str
    binding_sha256: str
    backend_type: str
    template_version: str
    expires_at: int


@dataclass
class _IssuedReceipt:
    canonical_payload: str
    expires_at: int
    used: bool = False


@dataclass(frozen=True)
class RouterRequest:
    task_id: str
    user_input: str
    backend_type: str
    backend_locator: str
    client_name: str
    knowledge_base_name: str
    subject_type: str
    confirmation: ConfirmationReceipt | None = None

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID")
        for name in ("user_input", "backend_type", "backend_locator", "client_name", "knowledge_base_name"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.subject_type not in SUBJECT_TYPES:
            raise ValueError("subject_type is unsupported")


@dataclass(frozen=True)
class RouterResponse:
    intent: str
    status: str
    code: str | None
    client_id: str | None
    preview: Mapping[str, Any]
    root_refs: tuple[Any, ...]
    confirmation: ConfirmationReceipt | None
    evidence: Mapping[str, Any]


def stable_client_id(backend_locator: str) -> str:
    digest = hashlib.sha256(backend_locator.strip().encode("utf-8")).hexdigest()[:14].upper()
    return f"CLT-{digest}"


def canonical_backend_locator(backend_type: str, locator: str) -> str | None:
    """在绑定、收据和预览之前规范后端位置。"""
    if locator.startswith("fake://"):
        return locator.strip()
    if backend_type == "obsidian":
        from .obsidian_adapter import canonical_obsidian_locator
        return canonical_obsidian_locator(locator)
    if backend_type != "feishu" or locator.startswith("fake://"):
        return locator.strip()
    try:
        parsed = urlsplit(locator)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if parsed.port is not None or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    if parsed.scheme != "https" or (host != "feishu.cn" and not host.endswith(".feishu.cn")):
        return None
    parts = tuple(part for part in parsed.path.split("/") if part)
    if len(parts) == 2 and parts[0] == "wiki" and _FEISHU_NODE.fullmatch(parts[1]):
        return f"https://{host}/wiki/{parts[1]}"
    if len(parts) == 3 and parts[:2] == ("wiki", "space") and _FEISHU_SPACE.fullmatch(parts[2]):
        return f"https://{host}/wiki/space/{parts[2]}"
    return None


def collect_intents(user_input: str) -> tuple[str, ...]:
    text = user_input.lower()
    matches = []
    if "创建" in text or "建库" in text or (("新建" in text or "搭建" in text) and "知识库" in text):
        matches.append("create")
    if "入库" in text or "资料" in text and ("放" in text or "上传" in text):
        matches.append("ingest")
    if "06" in text and ("更新" in text or "修改" in text or "工作流" in text):
        matches.append("update_06")
    if "状态" in text or "查看" in text or "检查" in text:
        matches.append("status")
    return tuple(matches)


def classify_intent(user_input: str) -> str:
    matches = collect_intents(user_input)
    if len(matches) > 1:
        return "routing_ambiguous"
    return matches[0] if matches else "needs_clarification"


class Stage2Router:
    """供首次建库复用的骨架编排器；不承载完整资料入库流程。"""

    def __init__(self, adapter: KnowledgeBaseAdapter, *, now: Callable[[], int] | None = None) -> None:
        self.adapter = adapter
        self._now = now or (lambda: int(time.time()))
        self._issued_receipts: dict[str, _IssuedReceipt] = {}
        self._intake = Stage5Intake(adapter)

    def ingest(self, request: IntakeRequest) -> IntakeResponse:
        return self._intake.execute(request)

    def execute(self, request: RouterRequest) -> RouterResponse:
        intent = classify_intent(request.user_input)
        evidence = RunEvidence(request.task_id, FAKE_RUN_ID, PHASE_ID, self._evidence_input(request), {"mode": intent})
        evidence.coverage = {
            "model": EXECUTION_MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "mode": intent,
            "backend": request.backend_type,
            "fake_adapter_only": True,
        }
        evidence.limitations = ("legacy_skeleton_entry", "full_runtime_owned_by_zsk_entry")
        self._record(evidence, "classify_intent", AdapterResult.ok(checked=(intent,)), "start", "intent_classified")
        if intent == "routing_ambiguous":
            return self._finish(evidence, intent, "blocked", "routing_ambiguous", None, {}, (), None, "routing_ambiguous", "检测到多个任务模式；请只说明一个当前动作。", blocked=True)
        if intent == "needs_clarification":
            return self._finish(evidence, intent, "needs_clarification", "routing_ambiguous", None, {}, (), None, "needs_clarification", "未识别建库、入库、状态或更新 06；请明确当前动作。")
        if intent == "ingest":
            return self._finish(evidence, intent, "unavailable", "source_unreadable", None, {}, (), None, "stage5_file_required", "这个兼容入口不携带文件；资料入库由 ZskEntry.ingest 接收显式附件。")
        if request.backend_type not in SUPPORTED_BACKENDS:
            return self._finish(evidence, intent, "blocked", "backend_unsupported", None, {}, (), None, "backend_unsupported", "后端不在阶段 2 的飞书/Obsidian范围内。", blocked=True)
        if locator_has_credential(request.backend_locator):
            return self._finish(evidence, intent, "blocked", "credential_locator", None, {}, (), None, "credential_locator", "目标位置的 query 或 fragment 含凭据形态；未注册或写入。", blocked=True)
        locator = canonical_backend_locator(request.backend_type, request.backend_locator)
        if locator is None:
            if request.backend_type == "obsidian":
                self._configure_evidence(evidence, "obsidian", "obsidian://invalid", intent)
                declaration = "真实 Obsidian 目标目录无效或不安全；未注册或写入。"
            else:
                declaration = "飞书目标链接不合法；未注册或写入。"
            return self._finish(evidence, intent, "blocked", "binding_missing", None, {}, (), None, "binding_locator_invalid", declaration, blocked=True)
        self._configure_evidence(evidence, request.backend_type, locator, intent)

        binding = self._binding(request, locator)
        doctor = self.adapter.doctor()
        self._record(evidence, "doctor", doctor, "intent_classified", "doctor_checked")
        if doctor.status not in {"ok", "reused"}:
            return self._from_failure(evidence, intent, binding.client_id, doctor, "doctor_failed")
        resolved = self.adapter.resolve_binding(binding)
        self._record(evidence, "resolve_binding", resolved, "doctor_checked", "binding_resolved")
        if resolved.status not in {"ok", "reused"}:
            return self._from_failure(evidence, intent, binding.client_id, resolved, "binding_failed")
        self._mark_backend_connected(evidence)
        structure = self.adapter.inspect_structure(binding)
        self._record(evidence, "inspect_structure", structure, "binding_resolved", "structure_inspected")
        if structure.status not in {"ok", "reused"}:
            return self._from_failure(evidence, intent, binding.client_id, structure, "structure_stopped")
        preview = self._preview(binding, structure)
        if intent == "status":
            declaration = self._declaration(evidence, "已完成只读结构检查；未创建或修改根对象。") if self._is_real_backend(evidence) else "阶段 2 状态已只读检查；未创建或修改根对象。"
            return self._finish(evidence, intent, "status", None, binding.client_id, preview, structure.object_refs, None, "status_read_only", declaration)
        if intent == "update_06":
            detail = "仅返回目录结构预览；未修改客户根对象。" if self._is_real_obsidian(evidence) else "仅生成 06 更新预览；未修改客户文档。"
            declaration = self._declaration(evidence, detail) if self._is_real_backend(evidence) else "阶段 2 仅预览 06；未修改客户所有文档。"
            return self._finish(evidence, intent, "preview_only", None, binding.client_id, preview, structure.object_refs, None, "update_06_preview_only", declaration)
        return self._create(evidence, request, binding, structure, preview)

    def _create(self, evidence: RunEvidence, request: RouterRequest, binding: Binding, structure: AdapterResult, preview: Mapping[str, Any]) -> RouterResponse:
        real = self._is_real_backend(evidence)
        if structure.status == "reused":
            if request.confirmation:
                receipt_error = self._validate_receipt(request.confirmation, binding, preview)
                if receipt_error is not None:
                    declaration = self._declaration(evidence, "确认收据无效、已使用或已过期；零写入。") if real else "确认收据无效、已使用或已过期；零写入。"
                    return self._finish(evidence, "create", "blocked", receipt_error, binding.client_id, preview, structure.object_refs, None, receipt_error, declaration, blocked=True)
            declaration = self._declaration(evidence, "结构已完整复用；未覆盖客户根对象。") if real else "结构已完整复用；未覆盖客户文档。"
            return self._finish(evidence, "create", "reused", None, binding.client_id, preview, structure.object_refs, None, "complete_structure_reused", declaration)
        if request.confirmation is None:
            receipt = self._issue_receipt(binding, preview)
            self._record(evidence, "preview", AdapterResult.ok(checked=("nine_root_preview", "confirmation_required")), "structure_inspected", "awaiting_confirmation")
            declaration = self._declaration(evidence, "已生成建库预览，等待明确确认；零写入。") if real else "已生成建库预览，等待明确确认；零写入。"
            return self._finish(evidence, "create", "confirmation_required", None, binding.client_id, preview, structure.object_refs, receipt, "awaiting_customer_confirmation", declaration)
        self._record(evidence, "preview", AdapterResult.ok(checked=("nine_root_preview", "receipt_rechecked")), "structure_inspected", "preview_rechecked")
        receipt_error = self._validate_receipt(request.confirmation, binding, preview)
        if receipt_error is not None:
            declaration = self._declaration(evidence, "确认收据无效或已过期；零写入。") if real else "确认收据无效或已过期；零写入。"
            return self._finish(evidence, "create", "blocked", receipt_error, binding.client_id, preview, structure.object_refs, None, receipt_error, declaration, blocked=True)
        self._issued_receipts[request.confirmation.token].used = True
        self._record(evidence, "confirm", AdapterResult.ok(checked=("receipt_bound", "one_time_use")), "structure_inspected", "confirmed")
        created = self.adapter.create_skeleton(binding)
        self._record(evidence, "create_skeleton", created, "confirmed", "skeleton_created")
        if created.status not in {"ok", "reused"}:
            return self._from_failure(evidence, "create", binding.client_id, created, "create_failed", preview)
        rules = self.adapter.read_rules(binding)
        self._record(evidence, "read_rules", rules, "skeleton_created", "rules_read")
        if rules.status not in {"ok", "reused"}:
            return self._from_failure(evidence, "create", binding.client_id, rules, "rules_read_failed_after_create", preview, created.object_refs, after_create=True)
        readback = self.adapter.read_back(binding, created.object_refs)
        self._record(evidence, "read_back", readback, "rules_read", "read_back_verified")
        if readback.status not in {"ok", "reused"}:
            return self._from_failure(evidence, "create", binding.client_id, readback, "readback_failed_after_create", preview, created.object_refs, after_create=True)
        evidence.output_refs.extend(ref.as_dict() for ref in readback.object_refs)
        reason = "real_feishu_created_and_readback" if self._is_real_feishu(evidence) else "real_obsidian_created_and_readback" if self._is_real_obsidian(evidence) else "stage2_boundary_after_create"
        declaration = self._declaration(evidence, "已创建并回读；") if real else "阶段 2 Fake 建库已创建并回读；未连接真实后端；未进入阶段 3。"
        return self._finish(evidence, "create", "created", None, binding.client_id, preview, readback.object_refs, None, reason, declaration)

    def _binding(self, request: RouterRequest, locator: str) -> Binding:
        return Binding(
            schema_version=BINDING_SCHEMA,
            client_id=stable_client_id(locator),
            client_name=request.client_name.strip(),
            knowledge_base_name=request.knowledge_base_name.strip(),
            subject_type=request.subject_type,
            backend_type=request.backend_type.strip(),
            backend_locator=locator,
            root_map={key: f"root:{key}" for key in ROOT_KEYS},
            template_version=TEMPLATE_VERSION,
        )

    @staticmethod
    def _configure_evidence(evidence: RunEvidence, backend_type: str, locator: str, intent: str) -> None:
        if locator.startswith("fake://"):
            return
        if backend_type == "feishu" and locator.startswith("https://"):
            evidence.phase_id = REAL_FEISHU_PHASE_ID
            evidence.coverage = {
                "model": EXECUTION_MODEL,
                "reasoning_effort": REASONING_EFFORT,
                "mode": intent,
                "backend": backend_type,
                "execution_mode": "real_feishu",
                "real_backend_connected": False,
            }
            evidence.limitations = ("skeleton_only", "content_flow_owned_by_zsk_entry")
            return
        if backend_type == "obsidian":
            evidence.phase_id = REAL_OBSIDIAN_PHASE_ID
            evidence.coverage = {
                "model": EXECUTION_MODEL,
                "reasoning_effort": REASONING_EFFORT,
                "mode": intent,
                "backend": backend_type,
                "execution_mode": "real_obsidian",
                "real_backend_connected": False,
            }
            evidence.limitations = ("skeleton_only", "content_flow_owned_by_zsk_entry")

    @staticmethod
    def _is_real_feishu(evidence: RunEvidence) -> bool:
        return evidence.phase_id == REAL_FEISHU_PHASE_ID

    @staticmethod
    def _is_real_obsidian(evidence: RunEvidence) -> bool:
        return evidence.phase_id == REAL_OBSIDIAN_PHASE_ID

    def _is_real_backend(self, evidence: RunEvidence) -> bool:
        return self._is_real_feishu(evidence) or self._is_real_obsidian(evidence)

    @staticmethod
    def _evidence_input(request: RouterRequest) -> str:
        if request.backend_type != "obsidian":
            return request.user_input
        raw = request.backend_locator.strip()
        redacted = request.user_input
        for value in sorted({raw, raw.rstrip("/")}, key=len, reverse=True):
            if value:
                redacted = redacted.replace(value, "[Obsidian目标目录]")
        return redacted

    def _mark_backend_connected(self, evidence: RunEvidence) -> None:
        if self._is_real_backend(evidence):
            evidence.coverage = {**evidence.coverage, "real_backend_connected": True}

    def _declaration(self, evidence: RunEvidence, detail: str) -> str:
        backend = "Feishu" if self._is_real_feishu(evidence) else "Obsidian"
        return f"真实 {backend} {detail.rstrip('。；')}；未执行资料入库、安装、推送或发布。"

    def _preview(self, binding: Binding, structure: AdapterResult) -> Mapping[str, Any]:
        missing = list(structure.metadata.get("missing_root_keys", ()))
        return {
            "client_id": binding.client_id,
            "client_name": binding.client_name,
            "knowledge_base_name": binding.knowledge_base_name,
            "subject_type": binding.subject_type,
            "backend_type": binding.backend_type,
            "backend_locator": "obsidian://root" if binding.backend_type == "obsidian" else binding.backend_locator,
            "missing_root_keys": missing,
            "existing_root_keys": list(structure.metadata.get("existing_root_keys", [ref.object_id.removeprefix("root:") for ref in structure.object_refs])),
            "template": dict(template_preview(binding)),
        }

    def _issue_receipt(self, binding: Binding, preview: Mapping[str, Any]) -> ConfirmationReceipt:
        preview_sha256 = self._sha(preview)
        binding_sha256 = self._sha(binding.as_dict())
        expires_at = self._now() + 300
        payload = {"preview_sha256": preview_sha256, "binding_sha256": binding_sha256, "backend_type": binding.backend_type, "template_version": binding.template_version, "expires_at": expires_at}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        token = self._sha({"payload": canonical, "sequence": len(self._issued_receipts) + 1})
        self._issued_receipts[token] = _IssuedReceipt(canonical, expires_at)
        return ConfirmationReceipt(token, preview_sha256, binding_sha256, binding.backend_type, binding.template_version, expires_at)

    def _validate_receipt(self, receipt: ConfirmationReceipt, binding: Binding, preview: Mapping[str, Any]) -> str | None:
        issued = self._issued_receipts.get(receipt.token)
        if issued is None:
            return "confirmation_mismatch"
        if issued.used:
            return "receipt_reused"
        payload = {"preview_sha256": receipt.preview_sha256, "binding_sha256": receipt.binding_sha256, "backend_type": receipt.backend_type, "template_version": receipt.template_version, "expires_at": receipt.expires_at}
        if json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != issued.canonical_payload or receipt.expires_at != issued.expires_at:
            return "confirmation_mismatch"
        if self._now() > issued.expires_at:
            return "receipt_expired"
        if receipt.backend_type != binding.backend_type or receipt.template_version != binding.template_version:
            return "confirmation_mismatch"
        if receipt.preview_sha256 != self._sha(preview):
            return "confirmation_mismatch"
        current_binding = self._sha(binding.as_dict())
        return None if receipt.binding_sha256 == current_binding else "confirmation_mismatch"

    @staticmethod
    def _sha(value: Mapping[str, Any]) -> str:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _record(evidence: RunEvidence, action: str, result: AdapterResult, before: str, after: str) -> None:
        state_before = evidence.events[-1]["state_after"] if evidence.events else "start"
        state_after = after if result.status in {"ok", "reused"} else f"{action}_{result.status}"
        evidence.record(action, result, state_before=state_before, state_after=state_after)

    def _from_failure(self, evidence: RunEvidence, intent: str, client_id: str, result: AdapterResult, reason: str, preview: Mapping[str, Any] | None = None, refs: tuple[Any, ...] = (), *, after_create: bool = False) -> RouterResponse:
        if self._is_real_backend(evidence):
            backend = "Feishu" if self._is_real_feishu(evidence) else "Obsidian"
            declaration = f"真实 {backend} 已因 {result.code} 停止；未继续下游，未执行资料入库、安装、推送或发布。"
            if after_create:
                declaration = f"真实 {backend} 根对象已在 create-only 操作提交；随后因 {result.code} 停止，当前状态需由下一次 status/readback 发现；未执行资料入库、安装、推送或发布。"
        else:
            declaration = f"阶段 2 已因 {result.code} 停止；未继续下游。"
            if after_create:
                declaration = f"9 根创建已在公开原子操作提交；随后因 {result.code} 停止，当前状态需由下一次 status/readback 发现。"
        return self._finish(evidence, intent, "blocked", result.code, client_id, preview or {}, refs, None, reason, declaration, blocked=True)

    @staticmethod
    def _finish(evidence: RunEvidence, intent: str, status: str, code: str | None, client_id: str | None, preview: Mapping[str, Any], refs: tuple[Any, ...], receipt: ConfirmationReceipt | None, reason: str, declaration: str, *, blocked: bool = False) -> RouterResponse:
        evidence.finish(stop_reason=reason, final_declaration=declaration, status="blocked" if blocked else "complete")
        return RouterResponse(intent, status, code, client_id, preview, refs, receipt, evidence.as_dict())
