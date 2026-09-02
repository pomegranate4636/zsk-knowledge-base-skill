"""阶段 8：把已登记的 Profile 来源写成可独立选择的 05 Profile。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .adapter import KnowledgeBaseAdapter
from .contracts import AssetPayload, BackendObjectRef, Binding, SourceRecord, TASK_ID


PROFILE_SCHEMA = "zsk-profile-v2"


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
    is_primary: bool = True
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID")
        if not isinstance(self.subject_name, str) or not self.subject_name.strip():
            raise ValueError("subject_name is required")
        if not isinstance(self.is_primary, bool):
            raise ValueError("is_primary must be boolean")
        if not isinstance(self.aliases, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in self.aliases
        ):
            raise ValueError("aliases must be a tuple of non-empty strings")
        folded = [value.strip().casefold() for value in self.aliases]
        if len(folded) != len(set(folded)) or self.subject_name.strip().casefold() in folded:
            raise ValueError("aliases must be unique and differ from subject_name")


@dataclass(frozen=True)
class ProfilePrimary:
    profile_id: str
    subject_name: str
    source_id: str
    source_title: str
    layers: ProfileLayers
    is_primary: bool = True
    aliases: tuple[str, ...] = ()

    def asset(self, source_role: str) -> AssetPayload:
        return AssetPayload(
            self.profile_id,
            f"{self.subject_name} Profile",
            self.body(),
            self.source_id,
            source_role,
            {
                "profile_schema": PROFILE_SCHEMA,
                "profile_status": "active",
                "is_primary": self.is_primary,
                "aliases": self.aliases,
                "asset_root": "05",
            },
        )

    def body(self) -> str:
        def section(title: str, values: tuple[str, ...]) -> str:
            return f"## {title}\n\n" + "\n".join(f"- {value.strip()}" for value in values)

        return "\n\n".join((
            (
                "---\n"
                "status: active\n"
                f"is_primary: {'true' if self.is_primary else 'false'}\n"
                f"profile_id: {self.profile_id}\n"
                f"profile_schema: {PROFILE_SCHEMA}\n"
                f"display_name: {json.dumps(self.subject_name, ensure_ascii=False)}\n"
                f"aliases: {json.dumps(list(self.aliases), ensure_ascii=False)}\n"
                f"source_id: \"{self.source_id}\"\n"
                f"---\n\n# {self.subject_name} Profile"
            ),
            section("确认事实", self.layers.confirmed_facts),
            section("运营设定", self.layers.operating_settings),
            section("候选素材（待确认）", self.layers.candidate_materials),
            f"## 来源\n\n- {self.source_title}",
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
    """只写 05；允许多个 active Profile，但每个绑定至多一个 primary。"""

    def __init__(self, adapter: KnowledgeBaseAdapter) -> None:
        self.adapter = adapter
        self._active: dict[tuple[str, str], _ActivePrimary] = {}
        self._primary_by_binding: dict[str, str] = {}

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
        fingerprint = primary.asset("profile_material").fingerprint()
        binding_key = self._binding_key(request.binding)
        active_key = (binding_key, primary.profile_id)
        active = self._active.get(active_key)
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
        existing_primary = self._primary_by_binding.get(binding_key)
        if request.is_primary and existing_primary not in {None, primary.profile_id}:
            self._record(evidence, "active_primary_guard", "blocked", "version_conflict")
            return ProfileResponse("exception", "version_conflict", None, evidence)
        written = self.adapter.write_profile(request.binding, primary.asset("profile_material"))
        self._record(evidence, "write_profile", written.status, written.code)
        if written.status not in {"ok", "reused"}:
            code = "version_conflict" if written.code == "duplicate_conflict" else written.code or "write_failed"
            return ProfileResponse("exception", code, None, evidence)
        readback = self.adapter.read_back(request.binding, written.object_refs)
        self._record(evidence, "read_back", readback.status, readback.code)
        if readback.status not in {"ok", "reused"}:
            return ProfileResponse("exception", readback.code or "readback_failed", None, evidence)
        self._active[active_key] = _ActivePrimary(fingerprint, primary, readback.object_refs)
        if request.is_primary:
            self._primary_by_binding[binding_key] = primary.profile_id
        evidence["status"] = "reused" if written.status == "reused" else "registered"
        evidence["profile_id"] = primary.profile_id
        evidence["downstream_asset_call_count"] = 1
        return ProfileResponse(evidence["status"], None, primary, evidence)

    @staticmethod
    def _request_code(request: ProfileRequest) -> str | None:
        if request.source.client_id != request.binding.client_id:
            return "binding_conflict"
        if request.source.source_role not in {"profile_material", "mixed", "unknown"}:
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
        identity = f"{request.binding.client_id}\n{request.subject_name.strip().casefold()}"
        profile_id = "PRF-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16].upper()
        return ProfilePrimary(
            profile_id,
            request.subject_name.strip(),
            request.source.source_id,
            request.source.source_title,
            request.layers,
            request.is_primary,
            tuple(value.strip() for value in request.aliases),
        )

    @staticmethod
    def _binding_key(binding: Binding) -> str:
        return hashlib.sha256(repr(sorted(binding.as_dict().items())).encode("utf-8")).hexdigest()

    @staticmethod
    def _record(evidence: dict[str, Any], action: str, status: str, code: str | None) -> None:
        evidence["events"].append({"action": action, "status": status, "code": code})
