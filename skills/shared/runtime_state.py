"""Durable, non-secret ZSK runtime state for cross-turn continuation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import secrets
import time
from typing import Callable

from .approval import ApprovalIO, FileApprovalIO
from .contracts import AdapterResult, BINDING_SCHEMA, ROOT_KEYS, Binding


BINDING_STATE_TYPE = "zsk_active_binding_v1"
READINESS_STATE_TYPE = "zsk_first_run_ready_v1"
BOOTSTRAP_CONFIRMATION_TYPE = "zsk_bootstrap_confirmation_v1"


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read_json(io: ApprovalIO, path: Path) -> dict | None:
    raw = io.read(path)
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("runtime_state_invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("runtime_state_invalid")
    return value


class BindingStore:
    def __init__(self, runtime_root: Path, *, io: ApprovalIO | None = None) -> None:
        self.path = runtime_root / "active-binding.json"
        self.io = io or FileApprovalIO()

    def save_active(self, binding: Binding) -> None:
        payload = {"type": BINDING_STATE_TYPE, "binding": binding.as_dict()}
        self.io.write_atomic(self.path, _canonical(payload))

    def load_active(self, *, required: bool = True) -> Binding | None:
        payload = _read_json(self.io, self.path)
        if payload is None:
            if required:
                raise ValueError("binding_missing")
            return None
        if set(payload) != {"type", "binding"} or payload.get("type") != BINDING_STATE_TYPE or not isinstance(payload.get("binding"), dict):
            raise ValueError("runtime_state_invalid")
        value = payload["binding"]
        try:
            return Binding(
                value["schema_version"], value["client_id"], value["client_name"], value["knowledge_base_name"],
                value["subject_type"], value["backend_type"], value["backend_locator"], value["root_map"],
                value["template_version"], value.get("status", "active"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("runtime_state_invalid") from exc


def _binding_fingerprint(binding: Binding) -> str:
    return hashlib.sha256(_canonical(binding.as_dict())).hexdigest()


class ReadinessStore:
    def __init__(self, runtime_root: Path, *, io: ApprovalIO | None = None) -> None:
        self.path = runtime_root / "first-run-ready-v1.json"
        self.io = io or FileApprovalIO()

    def mark_ready(self, binding: Binding) -> None:
        self.io.write_atomic(self.path, _canonical({"type": READINESS_STATE_TYPE, "status": "ready", "binding_sha256": _binding_fingerprint(binding)}))

    def is_ready(self, binding: Binding) -> bool:
        try:
            value = _read_json(self.io, self.path)
        except ValueError:
            return False
        return value == {"type": READINESS_STATE_TYPE, "status": "ready", "binding_sha256": _binding_fingerprint(binding)}

    def invalidate(self) -> None:
        self.io.write_atomic(self.path, _canonical({"type": READINESS_STATE_TYPE, "status": "invalid", "binding_sha256": None}))


class BootstrapConfirmationStore:
    def __init__(self, runtime_root: Path, *, io: ApprovalIO | None = None, now: Callable[[], int] | None = None) -> None:
        self.path = runtime_root / "bootstrap-confirmation.json"
        self.io = io or FileApprovalIO()
        self.now = now or (lambda: int(time.time()))

    @staticmethod
    def _preview_sha256(preview: dict[str, str]) -> str:
        return hashlib.sha256(_canonical(preview)).hexdigest()

    def issue(self, preview: dict[str, str], *, ttl_seconds: int = 900) -> str:
        if not isinstance(preview, dict) or not preview or ttl_seconds < 1:
            raise ValueError("confirmation_mismatch")
        token = secrets.token_urlsafe(24)
        record = {
            "type": BOOTSTRAP_CONFIRMATION_TYPE,
            "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "preview_sha256": self._preview_sha256(preview),
            "expires_at": self.now() + ttl_seconds,
            "used": False,
        }
        self.io.write_atomic(self.path, _canonical(record))
        return token

    def consume(self, token: str, preview: dict[str, str]) -> bool:
        record = _read_json(self.io, self.path)
        if not record or record.get("type") != BOOTSTRAP_CONFIRMATION_TYPE or record.get("used") is not False:
            return False
        valid = (
            isinstance(token, str)
            and hashlib.sha256(token.encode("utf-8")).hexdigest() == record.get("token_sha256")
            and self._preview_sha256(preview) == record.get("preview_sha256")
            and isinstance(record.get("expires_at"), int)
            and self.now() <= record["expires_at"]
        )
        if not valid:
            return False
        record["used"] = True
        self.io.write_atomic(self.path, _canonical(record))
        return True


@dataclass(frozen=True)
class ExistingBindingResult:
    status: str
    code: str | None
    binding: Binding | None
    root_refs: tuple = ()


class ExistingBindingService:
    """Bind an existing main knowledge base only after complete remote readback."""

    def __init__(self, bindings: BindingStore, readiness: ReadinessStore) -> None:
        self.bindings = bindings
        self.readiness = readiness

    def bind(self, binding: Binding, adapter) -> ExistingBindingResult:
        doctor = adapter.doctor()
        if doctor.status not in {"ok", "reused"}:
            if doctor.code in {"feishu_auth_missing", "permission_denied"}:
                self.readiness.invalidate()
            return ExistingBindingResult("blocked", doctor.code or "dependency_missing", None)
        resolved = adapter.resolve_binding(binding)
        if resolved.status not in {"ok", "reused"}:
            return ExistingBindingResult("blocked", resolved.code or "binding_missing", None)
        structure = adapter.inspect_structure(binding)
        if structure.status != "reused" or len(structure.object_refs) != len(ROOT_KEYS):
            return ExistingBindingResult("blocked", structure.code or "structure_conflict", None, structure.object_refs)
        rules = adapter.read_rules(binding)
        if rules.status not in {"ok", "reused"}:
            return ExistingBindingResult("blocked", rules.code or "readback_failed", None, structure.object_refs)
        self.bindings.save_active(binding)
        self.readiness.mark_ready(binding)
        return ExistingBindingResult("bound", None, binding, structure.object_refs)
