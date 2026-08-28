"""阶段 5 的零安装 01/02 入库闭环。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import PurePath
import re
from typing import Any

from .adapter import KnowledgeBaseAdapter
from .contracts import BINDING_SCHEMA, SOURCE_ROLES, SOURCE_SCHEMA, TASK_ID, BackendObjectRef, Binding, ExceptionRecord, SourceRecord
from .format_readers import DependencyMissing, readable_text


SUPPORTED_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json", ".html", ".htm", ".docx", ".pptx", ".xlsx", ".pdf"})
_TABLE_SUFFIXES = frozenset({".csv", ".xlsx"})
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_IDENTITY = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\w)")
_SENSITIVE = (_PHONE, _EMAIL, _IDENTITY)
_SAFE_NOTES = {
    "format_unsupported": "当前版本不支持该格式（或缺少该格式的可选依赖），未保存原件或正文。",
    "source_unreadable": "资料为空或无法安全读取，未保存原件或正文。",
    "conversion_failed": "资料无法按严格规则转换，未保存原件或正文。",
    "permission_denied": "资料处理权未获允许，未保存原件或正文。",
    "ownership_unknown": "资料归属尚未确认，未保存原件或正文。",
    "privacy_approval_required": "检测到敏感信息，尚未取得原件保存授权。",
    "duplicate_conflict": "相同资料的客户或来源角色存在冲突。",
    "version_conflict": "同名资料的版本关系尚未确认。",
    "write_failed": "资料写入未完整完成，已停止后续处理。",
    "readback_failed": "资料写后回读失败，已停止后续处理。",
}
_QUESTIONS = {
    "privacy_approval_required": "是否允许在该私有知识库中保存敏感原件？",
    "ownership_unknown": "请确认该资料的归属和处理权。",
    "permission_denied": "请确认是否允许处理该资料。",
    "version_conflict": "请确认它是否为已有来源的新版本。",
}


@dataclass(frozen=True)
class IntakeRequest:
    task_id: str
    binding: Binding
    file_name: str
    payload: bytes
    source_title: str
    source_role: str = "unknown"
    permission_status: str = "allowed"
    original_retention_approved: bool = False
    stable_source_locator: str | None = None
    confirmed_version_of: str | None = None

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID")
        if not self.file_name or self.file_name != PurePath(self.file_name).name or "/" in self.file_name or "\\" in self.file_name:
            raise ValueError("file_name must be a plain file name")
        if not isinstance(self.payload, bytes) or not self.source_title.strip():
            raise ValueError("payload bytes and source_title are required")
        if self.source_role not in SOURCE_ROLES:
            raise ValueError("source_role is unsupported")
        if self.permission_status not in {"allowed", "unknown", "denied"}:
            raise ValueError("permission_status is unsupported")


@dataclass(frozen=True)
class IntakeResponse:
    status: str
    code: str | None
    source_id: str
    refs: tuple[BackendObjectRef, ...]
    record: SourceRecord | None
    evidence: dict[str, Any]


class Stage5Intake:
    """只登记 01 或写 02；不做业务资产判断。"""

    def __init__(self, adapter: KnowledgeBaseAdapter) -> None:
        self.adapter = adapter
        self._names: dict[tuple[str, str], str] = {}
        self._roles: dict[tuple[str, str], str] = {}

    def execute(self, request: IntakeRequest) -> IntakeResponse:
        digest = hashlib.sha256(request.payload).hexdigest()
        source_id = f"SRC-{digest[:24]}"
        suffix = PurePath(request.file_name).suffix.lower()
        evidence = self._evidence(request, suffix, source_id)
        ready = self._ready(request.binding, evidence)
        if ready is not None:
            return IntakeResponse("exception", ready[0], source_id, (), None, evidence)
        if request.permission_status != "allowed":
            code = "permission_denied" if request.permission_status == "denied" else "ownership_unknown"
            return self._exception(request.binding, source_id, code, evidence)
        if suffix not in SUPPORTED_SUFFIXES:
            return self._exception(request.binding, source_id, "format_unsupported", evidence)
        try:
            body = self._readable(request.payload, suffix)
        except DependencyMissing:
            evidence["format_note"] = "PDF 需要宿主环境提供可选依赖 pypdf；缺失时按 format_unsupported 准确停止。"
            return self._exception(request.binding, source_id, "format_unsupported", evidence)
        except UnicodeError:
            return self._exception(request.binding, source_id, "conversion_failed", evidence)
        except (csv.Error, ValueError):
            return self._exception(request.binding, source_id, "source_unreadable", evidence)
        sensitive_count = sum(len(pattern.findall(body)) for pattern in _SENSITIVE)
        evidence["privacy"] = {"sensitive_match_count": sensitive_count, "original_retention_approved": request.original_retention_approved}
        if sensitive_count and not request.original_retention_approved:
            return self._exception(request.binding, source_id, "privacy_approval_required", evidence)
        prior_role = self._roles.get((request.binding.client_id, source_id))
        if prior_role is not None and prior_role != request.source_role:
            return self._exception(request.binding, source_id, "duplicate_conflict", evidence)
        name_key = (request.binding.client_id, request.file_name.casefold())
        prior_source = self._names.get(name_key)
        version_of = None
        if prior_source and prior_source != source_id:
            confirmed = request.confirmed_version_of == prior_source and bool(request.stable_source_locator)
            if not confirmed:
                return self._exception(request.binding, source_id, "version_conflict", evidence)
            version_of = prior_source
        privacy_status = "redacted" if sensitive_count else "passed"
        if sensitive_count:
            for pattern in _SENSITIVE:
                body = pattern.sub("[已脱敏]", body)
        readable = self._document(request, source_id, digest, privacy_status, version_of, body)
        record = SourceRecord(
            SOURCE_SCHEMA, source_id, request.binding.client_id, request.source_title.strip(), request.source_role,
            "table" if suffix in _TABLE_SUFFIXES else "text", request.file_name, digest,
            hashlib.sha256(readable).hexdigest(), privacy_status, request.permission_status, version_of,
            "registered", request.original_retention_approved or not sensitive_count,
        )
        original = self.adapter.store_original(request.binding, record, request.payload)
        self._event(evidence, "store_original", original.status, original.code)
        if original.status not in {"ok", "reused"}:
            return self._exception(request.binding, source_id, original.code or "write_failed", evidence)
        readable_result = self.adapter.store_readable(request.binding, record, readable)
        self._event(evidence, "store_readable", readable_result.status, readable_result.code)
        if readable_result.status not in {"ok", "reused"}:
            return self._exception(request.binding, source_id, readable_result.code or "write_failed", evidence, original.object_refs)
        refs = original.object_refs + readable_result.object_refs
        readback = self.adapter.read_back(request.binding, refs)
        self._event(evidence, "read_back", readback.status, readback.code)
        if readback.status not in {"ok", "reused"}:
            return self._exception(request.binding, source_id, readback.code or "readback_failed", evidence, refs)
        self._names[name_key] = source_id
        self._roles[(request.binding.client_id, source_id)] = request.source_role
        status = "reused" if original.status == readable_result.status == "reused" else "registered"
        evidence.update({"status": status, "code": None, "output_ref_ids": [ref.object_id for ref in refs]})
        return IntakeResponse(status, None, source_id, refs, record, evidence)

    def _ready(self, binding: Binding, evidence: dict[str, Any]) -> tuple[str, str] | None:
        for action, call in (("doctor", self.adapter.doctor), ("resolve_binding", lambda: self.adapter.resolve_binding(binding)), ("inspect_structure", lambda: self.adapter.inspect_structure(binding))):
            result = call()
            self._event(evidence, action, result.status, result.code)
            if result.status not in {"ok", "reused"}:
                evidence.update({"status": "exception", "code": result.code})
                return result.code or "write_failed", action
            if action == "inspect_structure" and result.status != "reused":
                evidence.update({"status": "exception", "code": "structure_conflict"})
                return "structure_conflict", action
        return None

    def _exception(self, binding: Binding, source_id: str, code: str, evidence: dict[str, Any], refs: tuple[BackendObjectRef, ...] = ()) -> IntakeResponse:
        note = _SAFE_NOTES.get(code, "资料未能安全登记，已停止后续处理。")
        question = _QUESTIONS.get(code, "请确认资料后再重试。")
        exception_id = "EXC-" + hashlib.sha256(f"{source_id}:{code}".encode()).hexdigest()[:16]
        result = self.adapter.write_exception(binding, ExceptionRecord(exception_id, source_id, code, note, question, refs))
        self._event(evidence, "write_exception", result.status, result.code)
        final_code = code if result.status in {"ok", "reused"} else result.code or "write_failed"
        output_refs = refs + result.object_refs
        evidence.update({"status": "exception", "code": final_code, "exception_id": exception_id, "output_ref_ids": [ref.object_id for ref in output_refs]})
        return IntakeResponse("exception", final_code, source_id, output_refs, None, evidence)

    @staticmethod
    def _readable(payload: bytes, suffix: str) -> str:
        if suffix not in {".md", ".txt", ".csv"}:
            return readable_text(payload, suffix)
        text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip() or "\x00" in text:
            raise ValueError("empty or binary input")
        if suffix != ".csv":
            return text.rstrip() + "\n"
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        if not rows or not rows[0] or any(not cell.strip() for cell in rows[0]):
            raise ValueError("CSV requires a non-empty header")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("CSV column count is inconsistent")
        escaped = [[cell.replace("|", "\\|").replace("\n", " ") for cell in row] for row in rows]
        lines = ["| " + " | ".join(escaped[0]) + " |", "| " + " | ".join("---" for _ in escaped[0]) + " |"]
        lines.extend("| " + " | ".join(row) + " |" for row in escaped[1:])
        return "\n".join(lines) + "\n"

    @staticmethod
    def _document(request: IntakeRequest, source_id: str, original_sha256: str, privacy_status: str, version_of: str | None, body: str) -> bytes:
        suffix = PurePath(request.file_name).suffix.lower()
        unit_kind, unit_count = Stage5Intake._content_units(suffix, body)
        fields = {
            "source_id": source_id, "source_title": request.source_title.strip(),
            "original_file_name": request.file_name, "source_format": suffix.removeprefix("."),
            "source_role": request.source_role,
            "original_sha256": original_sha256, "privacy_status": privacy_status,
            "permission_status": request.permission_status, "original_retention_approved": request.original_retention_approved or privacy_status == "passed",
            "version_of": version_of, "content_unit_kind": unit_kind, "content_unit_count": unit_count,
        }
        frontmatter = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
        return f"---\n{frontmatter}\n---\n\n{body}".encode("utf-8")

    @staticmethod
    def _content_units(suffix: str, body: str) -> tuple[str | None, int | None]:
        if suffix == ".pptx":
            return "slides", len(re.findall(r"(?m)^## 幻灯片 \d+$", body))
        if suffix == ".pdf":
            return "pages", len(re.findall(r"(?m)^## 第 \d+ 页$", body))
        if suffix == ".xlsx":
            return "worksheets", len(re.findall(r"(?m)^## 工作表：", body))
        return None, None

    @staticmethod
    def _evidence(request: IntakeRequest, suffix: str, source_id: str) -> dict[str, Any]:
        return {
            "schema_version": "zsk-stage5-intake-evidence-v1", "task_id": request.task_id, "phase_id": "ZSK-P5",
            "safe_input_summary": f"stage5:{suffix.removeprefix('.') or 'none'}:{request.source_role}", "source_id": source_id,
            "events": [], "privacy": {"sensitive_match_count": 0, "original_retention_approved": request.original_retention_approved},
            "model_call_count": 0, "downstream_asset_call_count": 0,
        }

    @staticmethod
    def _event(evidence: dict[str, Any], action: str, status: str, code: str | None) -> None:
        evidence["events"].append({"action": action, "status": status, "code": code})
