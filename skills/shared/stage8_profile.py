"""阶段 8：把已登记的 Profile 来源写成单一、可回读的 05 主 Profile。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from .adapter import KnowledgeBaseAdapter
from .contracts import AssetPayload, BackendObjectRef, Binding, SourceRecord, TASK_ID


PROFILE_SCHEMA = "zsk-profile-primary-v1"


@dataclass(frozen=True)
class ProfileLayers:
    """三层内容必须分开输入，候选素材不自动升级为确认事实。"""

    confirmed_facts: tuple[str, ...]
    operating_settings: tuple[str, ...]
    candidate_materials: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("confirmed_facts", "operating_settings", "candidate_materials"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or not values or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{field_name} must be a non-empty tuple of strings")

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "confirmed_facts": [value.strip() for value in self.confirmed_facts],
            "operating_settings": [value.strip() for value in self.operating_settings],
            "candidate_materials": [value.strip() for value in self.candidate_materials],
        }


@dataclass(frozen=True)
class ProfileRequest:
    task_id: str
    binding: Binding
    source: SourceRecord
    subject_name: str
    layers: ProfileLayers
    evidence_pages: tuple[int, ...] = ()
    policy_id: str = ""
    policy_receipt: str = ""

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID")
        if not isinstance(self.subject_name, str) or not self.subject_name.strip():
            raise ValueError("subject_name is required")
        if any(not isinstance(page, int) or page < 1 for page in self.evidence_pages):
            raise ValueError("evidence_pages must contain positive page numbers")


@dataclass(frozen=True)
class ProfilePrimary:
    profile_id: str
    subject_name: str
    source_id: str
    layers: ProfileLayers
    evidence_pages: tuple[int, ...] = ()
    policy_id: str = ""
    policy_receipt: str = ""

    def asset(self, source_role: str) -> AssetPayload:
        metadata = {"profile_schema": PROFILE_SCHEMA, "primary_status": "active", "asset_root": "05"}
        if self.policy_id:
            metadata.update({"policy_id": self.policy_id, "policy_receipt": self.policy_receipt, "evidence_pages": list(self.evidence_pages)})
        return AssetPayload(self.profile_id, f"{self.subject_name} Profile", self.body(), self.source_id, source_role, metadata)

    def body(self) -> str:
        def section(title: str, values: tuple[str, ...]) -> str:
            return f"## {title}\n\n" + "\n".join(f"- {value.strip()}" for value in values)

        return "\n\n".join((
            f"# {self.subject_name} Profile",
            section("确认事实", self.layers.confirmed_facts),
            section("运营设定", self.layers.operating_settings),
            section("候选素材（待确认）", self.layers.candidate_materials),
            "## 来源\n\n" + ("\n".join(f"- `{self.source_id}` · 第 {page} 页 · `page-{page:03d}.png`" for page in self.evidence_pages) or f"- `{self.source_id}`"),
        )) + "\n"


@dataclass(frozen=True)
class ProfileResponse:
    status: str
    code: str | None
    primary: ProfilePrimary | None
    evidence: dict[str, Any]


@dataclass(frozen=True)
class _ActivePrimary:
    fingerprint: str
    primary: ProfilePrimary
    refs: tuple[BackendObjectRef, ...]


class Stage8Profile:
    """只写 05；同一运行时中每个绑定至多确认一份 active primary。"""

    def __init__(self, adapter: KnowledgeBaseAdapter) -> None:
        self.adapter = adapter
        self._active: dict[str, _ActivePrimary] = {}

    def execute(self, request: ProfileRequest) -> ProfileResponse:
        evidence: dict[str, Any] = {
            "schema_version": "zsk-stage8-evidence-v1",
            "task_id": request.task_id,
            "source_id": request.source.source_id,
            "output_root": "05",
            "events": [],
            "model_call_count": 0,
            "downstream_asset_call_count": 0,
        }
        for action, call in (
            ("doctor", self.adapter.doctor),
            ("resolve_binding", lambda: self.adapter.resolve_binding(request.binding)),
            ("inspect_structure", lambda: self.adapter.inspect_structure(request.binding)),
        ):
            result = call()
            self._record(evidence, action, result.status, result.code)
            if result.status not in {"ok", "reused"} or action == "inspect_structure" and result.status != "reused":
                return ProfileResponse("exception", result.code or "structure_conflict", None, evidence)
        code = self._request_code(request)
        if code is not None:
            self._record(evidence, "profile_gate", "blocked", code)
            return ProfileResponse("exception", code, None, evidence)
        primary = self._primary(request)
        fingerprint = primary.asset(request.source.source_role).fingerprint()
        binding_key = self._binding_key(request.binding)
        active = self._active.get(binding_key)
        if active is not None:
            if active.fingerprint != fingerprint:
                self._record(evidence, "active_primary_guard", "blocked", "version_conflict")
                return ProfileResponse("exception", "version_conflict", None, evidence)
            readback = self.adapter.read_back(request.binding, active.refs)
            self._record(evidence, "read_back", readback.status, readback.code)
            if readback.status not in {"ok", "reused"}:
                return ProfileResponse("exception", readback.code or "readback_failed", None, evidence)
            evidence["status"] = "reused"
            evidence["profile_id"] = active.primary.profile_id
            return ProfileResponse("reused", None, active.primary, evidence)
        written = self.adapter.write_profile(request.binding, primary.asset(request.source.source_role))
        self._record(evidence, "write_profile", written.status, written.code)
        if written.status not in {"ok", "reused"}:
            code = "version_conflict" if written.code == "duplicate_conflict" else written.code or "write_failed"
            return ProfileResponse("exception", code, None, evidence)
        readback = self.adapter.read_back(request.binding, written.object_refs)
        self._record(evidence, "read_back", readback.status, readback.code)
        if readback.status not in {"ok", "reused"}:
            return ProfileResponse("exception", readback.code or "readback_failed", None, evidence)
        self._active[binding_key] = _ActivePrimary(fingerprint, primary, readback.object_refs)
        evidence["status"] = "reused" if written.status == "reused" else "registered"
        evidence["profile_id"] = primary.profile_id
        evidence["downstream_asset_call_count"] = 1
        return ProfileResponse(evidence["status"], None, primary, evidence)

    @staticmethod
    def _request_code(request: ProfileRequest) -> str | None:
        if request.source.client_id != request.binding.client_id:
            return "binding_conflict"
        if request.subject_name.strip() != request.binding.client_name.strip():
            return "profile_identity_mismatch"
        if request.source.source_role != "profile_material":
            return "routing_ambiguous"
        if request.source.status not in {"registered", "reused"}:
            return "ownership_unknown"
        if request.source.permission_status != "allowed":
            return "permission_denied"
        if request.source.privacy_status not in {"passed", "redacted"}:
            return "privacy_blocked"
        return None

    @staticmethod
    def _primary(request: ProfileRequest) -> ProfilePrimary:
        profile_id = "PRF-" + hashlib.sha256(request.binding.client_id.encode("utf-8")).hexdigest()[:16]
        return ProfilePrimary(profile_id, request.subject_name.strip(), request.source.source_id, request.layers, request.evidence_pages, request.policy_id, request.policy_receipt)

    @staticmethod
    def _binding_key(binding: Binding) -> str:
        return hashlib.sha256(repr(sorted(binding.as_dict().items())).encode("utf-8")).hexdigest()

    @staticmethod
    def _record(evidence: dict[str, Any], action: str, status: str, code: str | None) -> None:
        evidence["events"].append({"action": action, "status": status, "code": code})
