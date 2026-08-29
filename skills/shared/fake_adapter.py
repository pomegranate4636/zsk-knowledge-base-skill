"""只在内存中运行的 Fake Adapter，用于阶段 1 合同验证。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Sequence

from .contracts import (
    AdapterResult,
    AssetPayload,
    BackendObjectRef,
    Binding,
    BindingRegistry,
    ExceptionRecord,
    PageArtifact,
    SourceRecord,
    payload_sha256,
)
from .templates import ROOT_TITLES, root_content, root_payload_fingerprint, template_fingerprint


@dataclass(frozen=True)
class FakeFaults:
    dependency_missing: bool = False
    permission_denied: bool = False
    backend_error_on: str | None = None
    readback_failure: bool = False
    initial_structure: str = "empty"
    tamper_on_readback: str | None = None


class FakeAdapter:
    """模拟后端的 create-only、幂等、冲突和 fail-closed 行为。"""

    ROOT_KEYS = ("01", "02", "03", "04", "05", "06", "07", "AGENTS", "README")

    def __init__(self, faults: FakeFaults | None = None) -> None:
        self.faults = faults or FakeFaults()
        self.registry = BindingRegistry()
        self.calls: list[str] = []
        self._active_binding: Binding | None = None
        self._objects: dict[str, dict[str, Any]] = {}
        self._registered_sources: dict[tuple[str, str], SourceRecord] = {}
        self._create_lock = Lock()

    @property
    def object_count(self) -> int:
        return len(self._objects)

    @property
    def active_binding(self) -> Binding | None:
        return self._active_binding

    def _guard(self, method: str) -> AdapterResult | None:
        self.calls.append(method)
        if self.faults.dependency_missing:
            return AdapterResult.failed("dependency_missing", f"fake dependency missing during {method}", blocked=True)
        if self.faults.permission_denied:
            return AdapterResult.failed("permission_denied", f"fake permission denied during {method}", blocked=True)
        if self.faults.backend_error_on == method:
            return AdapterResult.failed("write_failed", f"injected fake backend error during {method}")
        return None

    def _binding_guard(self, binding: Binding) -> AdapterResult | None:
        if self._active_binding is None:
            return AdapterResult.failed("binding_missing", "binding was not resolved", blocked=True)
        if self._active_binding.as_dict() != binding.as_dict():
            return AdapterResult.failed("binding_conflict", "resolved binding differs from requested binding", blocked=True)
        return None

    def _skeleton_refs(self) -> tuple[BackendObjectRef, ...]:
        return tuple(self._ref(f"root:{key}") for key in self.ROOT_KEYS if f"root:{key}" in self._objects)

    def _ref(self, object_key: str) -> BackendObjectRef:
        item = self._objects[object_key]
        return BackendObjectRef(item["object_id"], item["object_kind"], item["locator"], item["version"])

    def _store_object(self, key: str, object_kind: str, payload: MappingLike, *, locator: str | None = None) -> BackendObjectRef:
        record = {
            "object_id": key,
            "object_kind": object_kind,
            "locator": locator or f"fake://memory/{key}",
            "version": "1",
            **dict(payload),
        }
        self._objects[key] = record
        return self._ref(key)

    def _store_root(self, binding: Binding, key: str) -> BackendObjectRef:
        content = root_content(binding, key)
        return self._store_object(
            f"root:{key}",
            "logical_root",
            {
                "logical_key": key,
                "title": ROOT_TITLES[key],
                "binding_id": binding.client_id,
                "template_fingerprint": template_fingerprint(content),
                "content": content,
                "payload_fingerprint": root_payload_fingerprint(binding, key, content),
            },
        )

    def _seed_structure(self, binding: Binding) -> None:
        variant = self.faults.initial_structure
        if variant == "empty" or self._objects:
            return
        keys = self.ROOT_KEYS[:-1] if variant == "partial" else self.ROOT_KEYS
        for key in keys:
            self._store_root(binding, key)
        if variant == "heterogeneous":
            self._objects["root:01"]["object_kind"] = "unexpected_root_type"
        elif variant == "duplicate":
            self._store_object("root:01:duplicate", "logical_root", dict(self._objects["root:01"]))
        elif variant == "customer_modified":
            self._objects["root:AGENTS"]["content"] = "客户已修改的 AGENTS 内容"

    def _structure_state(self, binding: Binding) -> tuple[str, tuple[str, ...]]:
        duplicates = [key for key in self._objects if key.startswith("root:") and key.count(":") != 1]
        if duplicates:
            return "conflict", ()
        present = []
        for key in self.ROOT_KEYS:
            record = self._objects.get(f"root:{key}")
            if record is None:
                continue
            if record.get("object_kind") != "logical_root" or record.get("logical_key") != key or record.get("binding_id") != binding.client_id:
                return "conflict", ()
            content = record.get("content")
            if not isinstance(content, str) or record.get("template_fingerprint") != template_fingerprint(content):
                return "conflict", ()
            present.append(key)
        if not present:
            return "empty", tuple(self.ROOT_KEYS)
        missing = tuple(key for key in self.ROOT_KEYS if key not in present)
        return ("partial", missing) if missing else ("complete", ())

    def doctor(self) -> AdapterResult:
        blocked = self._guard("doctor")
        if blocked:
            return blocked
        return AdapterResult.ok(checked=("fake_adapter_loaded", "dependency_probe", "permission_probe"))

    def resolve_binding(self, binding: Binding) -> AdapterResult:
        blocked = self._guard("resolve_binding")
        if blocked:
            return blocked
        if self._active_binding is not None:
            same_client = self._active_binding.client_id == binding.client_id
            same_target = self._active_binding.backend_type == binding.backend_type and self._active_binding.backend_locator == binding.backend_locator
            same_subject = self._active_binding.subject_type == binding.subject_type
            if not (same_client and same_target and same_subject):
                return AdapterResult.failed("binding_conflict", "Fake Adapter locks one binding for one execution", blocked=True)
        outcome = self.registry.register(binding)
        if outcome.status == "conflict":
            return AdapterResult.failed("binding_conflict", "client or backend locator is already bound", blocked=True)
        self._active_binding = outcome.binding
        self._seed_structure(binding)
        if outcome.status == "reused":
            return AdapterResult.reused(checked=("client_id", "backend_locator", "binding_identity"))
        return AdapterResult.ok(checked=("client_id", "backend_locator", "binding_identity"))

    def inspect_structure(self, binding: Binding) -> AdapterResult:
        blocked = self._guard("inspect_structure")
        if blocked:
            return blocked
        blocked = self._binding_guard(binding)
        if blocked:
            return blocked
        state, missing = self._structure_state(binding)
        refs = self._skeleton_refs()
        if state == "conflict":
            return AdapterResult.failed("structure_conflict", "root types, duplicates or customer-owned documents conflict", blocked=True)
        if state == "empty":
            return AdapterResult.ok(checked=("nine_root_objects_absent",), metadata={"structure_state": state, "missing_root_keys": list(missing)})
        if state == "partial":
            return AdapterResult.ok(*refs, checked=("partial_root_objects",), metadata={"structure_state": state, "missing_root_keys": list(missing)})
        return AdapterResult.reused(*refs, checked=("nine_root_objects_present",), metadata={"structure_state": state, "missing_root_keys": []})

    def create_skeleton(self, binding: Binding) -> AdapterResult:
        with self._create_lock:
            blocked = self._guard("create_skeleton")
            if blocked:
                return blocked
            blocked = self._binding_guard(binding)
            if blocked:
                return blocked
            state, missing = self._structure_state(binding)
            if state == "conflict":
                return AdapterResult.failed("structure_conflict", "root types, duplicates or customer-owned documents conflict", blocked=True)
            if state == "complete":
                return AdapterResult.reused(*self._skeleton_refs(), checked=("create_only", "nine_root_objects_present"))
            for key in missing:
                self._store_root(binding, key)
            checked = ("create_only", "nine_root_objects_created") if state == "empty" else ("create_only", "missing_root_objects_created", "nine_root_objects_present")
            return AdapterResult.ok(*self._skeleton_refs(), checked=checked)

    def read_rules(self, binding: Binding) -> AdapterResult:
        blocked = self._guard("read_rules")
        if blocked:
            return blocked
        blocked = self._binding_guard(binding)
        if blocked:
            return blocked
        state, _missing = self._structure_state(binding)
        if state != "complete":
            return AdapterResult.failed("structure_conflict", "rules cannot be read before a complete skeleton exists", blocked=True)
        return AdapterResult.ok(self._ref("root:AGENTS"), self._ref("root:README"), checked=("rules_read", "customer_owned_rules_preserved"))

    def _ensure_write(self, method: str, binding: Binding) -> AdapterResult | None:
        blocked = self._guard(method)
        if blocked:
            return blocked
        blocked = self._binding_guard(binding)
        if blocked:
            return blocked
        state, _missing = self._structure_state(binding)
        if state != "complete":
            return AdapterResult.failed("structure_conflict", "formal assets require a complete skeleton", blocked=True)
        return None

    def _source_object_refs(self, source_id: str) -> tuple[BackendObjectRef, ...]:
        return tuple(
            self._ref(key)
            for key in (f"source:{source_id}:original", f"source:{source_id}:readable")
            if key in self._objects
        )

    def _refresh_source_registration(self, source: SourceRecord) -> None:
        keys = (f"source:{source.source_id}:original", f"source:{source.source_id}:readable")
        if all(key in self._objects for key in keys):
            records = [self._objects[key] for key in keys]
            if all(
                record.get("binding_id") == source.client_id
                and record.get("client_id") == source.client_id
                and record.get("source_role") == source.source_role
                for record in records
            ) and source.privacy_status in {"passed", "redacted"} and source.permission_status == "allowed" and source.status in {"registered", "reused"}:
                self._registered_sources[(source.client_id, source.source_id)] = source

    def _source_gate(self, binding: Binding, source_id: str) -> AdapterResult | None:
        if (binding.client_id, source_id) not in self._registered_sources:
            return AdapterResult.failed(
                "ownership_unknown",
                "formal asset requires a legally registered source under the active binding",
                blocked=True,
            )
        return None

    def _store_source(self, method: str, binding: Binding, source: SourceRecord, payload: bytes, kind: str) -> AdapterResult:
        blocked = self._ensure_write(method, binding)
        if blocked:
            return blocked
        if source.client_id != binding.client_id:
            return AdapterResult.failed("binding_conflict", "source client_id differs from the active binding", blocked=True)
        digest = payload_sha256(payload)
        if kind == "original" and digest != source.original_sha256:
            return AdapterResult.failed("source_unreadable", "original payload hash does not match source record", blocked=True)
        if kind == "readable" and digest != source.readable_sha256:
            return AdapterResult.failed("source_unreadable", "readable payload hash does not match source record", blocked=True)
        key = f"source:{source.source_id}:{kind}"
        existing = self._objects.get(key)
        if existing is not None:
            if existing["payload_sha256"] == digest and existing["client_id"] == source.client_id and existing["source_role"] == source.source_role:
                self._refresh_source_registration(source)
                return AdapterResult.reused(self._ref(key), checked=("source_id", "payload_sha256", "source_role"))
            return AdapterResult.failed("version_conflict", "same source_id cannot be overwritten", blocked=True)
        for other_key, other in self._objects.items():
            if not other_key.endswith(f":{kind}") or "payload_sha256" not in other:
                continue
            if other["payload_sha256"] == digest:
                if other["client_id"] == source.client_id and other["source_role"] == source.source_role:
                    return AdapterResult.reused(self._ref(other_key), checked=("same_bytes", "same_client", "same_source_role"))
                return AdapterResult.failed("duplicate_conflict", "same bytes belong to a different client or source role", blocked=True)
        ref = self._store_object(
            key,
            f"source_{kind}",
            {
                "source_id": source.source_id,
                "client_id": source.client_id,
                "binding_id": binding.client_id,
                "source_role": source.source_role,
                "payload_sha256": digest,
                "payload_fingerprint": digest,
                "payload": payload,
            },
        )
        self._refresh_source_registration(source)
        return AdapterResult.ok(ref, checked=("create_only", "source_id", "payload_sha256", "source_role"))

    def store_original(self, binding: Binding, source: SourceRecord, payload: bytes) -> AdapterResult:
        return self._store_source("store_original", binding, source, payload, "original")

    def store_readable(self, binding: Binding, source: SourceRecord, payload: bytes) -> AdapterResult:
        return self._store_source("store_readable", binding, source, payload, "readable")

    def store_page_evidence(self, binding: Binding, source: SourceRecord, page: PageArtifact, payload: bytes) -> AdapterResult:
        blocked = self._ensure_write("store_page_evidence", binding)
        if blocked:
            return blocked
        if source.client_id != binding.client_id or page.source_id != source.source_id or page not in source.page_artifacts:
            return AdapterResult.failed("binding_conflict", "page evidence does not belong to the active source", blocked=True)
        digest = payload_sha256(payload)
        if digest != page.sha256:
            return AdapterResult.failed("source_unreadable", "page payload hash does not match its manifest", blocked=True)
        key = f"source:{source.source_id}:page:{page.page_number:03d}"
        existing = self._objects.get(key)
        if existing is not None:
            if existing["payload_sha256"] == digest:
                return AdapterResult.reused(self._ref(key), checked=("page_number", "payload_sha256", "source_id"))
            return AdapterResult.failed("version_conflict", "page evidence cannot overwrite prior content", blocked=True)
        ref = self._store_object(
            key,
            "source_page",
            {
                "source_id": source.source_id,
                "client_id": source.client_id,
                "binding_id": binding.client_id,
                "source_role": source.source_role,
                "payload_sha256": digest,
                "payload_fingerprint": digest,
                "payload": payload,
            },
        )
        return AdapterResult.ok(ref, checked=("create_only", "page_number", "payload_sha256", "source_id"))

    def write_exception(self, binding: Binding, exception: ExceptionRecord) -> AdapterResult:
        blocked = self._ensure_write("write_exception", binding)
        if blocked:
            return blocked
        key = f"exception:{exception.exception_id}"
        existing = self._objects.get(key)
        fingerprint = exception.fingerprint()
        if existing is not None:
            if existing["fingerprint"] == fingerprint:
                return AdapterResult.reused(self._ref(key), checked=("exception_id", "safe_note", "question", "reason_code", "source_link"))
            return AdapterResult.failed("write_failed", "exception id collision cannot overwrite prior record", blocked=True)
        source_refs = exception.source_refs or self._source_object_refs(exception.source_id)
        exception_data = {
            "exception_id": exception.exception_id,
            "source_id": exception.source_id,
            "reason_code": exception.reason_code,
            "safe_note": exception.safe_note,
            "question": exception.question,
            "source_refs": [ref.as_dict() for ref in source_refs],
        }
        ref = self._store_object(
            key,
            "exception",
            {
                "binding_id": binding.client_id,
                "fingerprint": fingerprint,
                "payload_fingerprint": fingerprint,
                "exception_data": exception_data,
            },
        )
        return AdapterResult.ok(
            ref,
            checked=("exception_id", "safe_note", "question", "reason_code", "source_link", "no_sensitive_body"),
            metadata={"exception_fields": ("exception_id", "source_id", "reason_code", "safe_note", "question"), "source_ref_ids": tuple(ref.object_id for ref in source_refs)},
        )

    def _write_asset(self, method: str, binding: Binding, asset: AssetPayload, kind: str) -> AdapterResult:
        blocked = self._ensure_write(method, binding)
        if blocked:
            return blocked
        blocked = self._source_gate(binding, asset.source_id)
        if blocked:
            return blocked
        key = f"asset:{kind}:{asset.asset_id}"
        fingerprint = asset.fingerprint()
        existing = self._objects.get(key)
        if existing is not None:
            if existing["fingerprint"] == fingerprint:
                return AdapterResult.reused(self._ref(key), checked=("asset_id", "content_fingerprint", "source_id"))
            return AdapterResult.failed("duplicate_conflict", "asset id collision cannot overwrite prior asset", blocked=True)
        ref = self._store_object(
            key,
            f"asset_{kind}",
            {
                "binding_id": binding.client_id,
                "fingerprint": fingerprint,
                "payload_fingerprint": fingerprint,
                "asset_data": {
                    "asset_id": asset.asset_id,
                    "title": asset.title,
                    "body": asset.body,
                    "source_id": asset.source_id,
                    "source_role": asset.source_role,
                },
                "source_id": asset.source_id,
            },
        )
        return AdapterResult.ok(ref, checked=("create_only", "asset_id", "content_fingerprint", "source_id", "registered_source"))

    def write_knowledge_asset(self, binding: Binding, asset: AssetPayload) -> AdapterResult:
        return self._write_asset("write_knowledge_asset", binding, asset, "knowledge")

    def write_method_asset(self, binding: Binding, asset: AssetPayload) -> AdapterResult:
        return self._write_asset("write_method_asset", binding, asset, "method")

    def write_profile(self, binding: Binding, asset: AssetPayload) -> AdapterResult:
        return self._write_asset("write_profile", binding, asset, "profile")

    def read_back(self, binding: Binding, refs: Sequence[BackendObjectRef] | None = None) -> AdapterResult:
        blocked = self._guard("read_back")
        if blocked:
            return blocked
        blocked = self._binding_guard(binding)
        if blocked:
            return blocked
        if self.faults.readback_failure:
            return AdapterResult.failed("readback_failed", "injected fake readback failure", blocked=True)
        target_refs = tuple(refs or self._skeleton_refs())
        if self.faults.tamper_on_readback and target_refs:
            item = self._objects.get(target_refs[0].object_id)
            if item is not None:
                if self.faults.tamper_on_readback == "locator":
                    item["locator"] = "fake://memory/tampered"
                elif self.faults.tamper_on_readback == "version":
                    item["version"] = "2"
                elif self.faults.tamper_on_readback == "payload":
                    item["payload"] = b"tampered"
                else:
                    item["content"] = "tampered"
        for ref in target_refs:
            if ref.object_id not in self._objects:
                return AdapterResult.failed("readback_failed", "requested object is missing after write", blocked=True)
            if not self._verify_object(ref, binding):
                return AdapterResult.failed("readback_failed", "object identity, binding or payload fingerprint changed", blocked=True)
        return AdapterResult.ok(*tuple(self._ref(ref.object_id) for ref in target_refs), checked=("objects_present", "binding_identity", "stable_refs", "payload_hashes_or_fingerprints"))

    def _verify_object(self, ref: BackendObjectRef, binding: Binding) -> bool:
        item = self._objects.get(ref.object_id)
        if item is None:
            return False
        if (
            item.get("object_id") != ref.object_id
            or item.get("object_kind") != ref.object_kind
            or item.get("locator") != ref.locator
            or item.get("version") != ref.version
            or item.get("binding_id") != binding.client_id
        ):
            return False
        expected = item.get("payload_fingerprint")
        if not isinstance(expected, str):
            return False
        try:
            if item.get("object_kind", "").startswith("source_"):
                payload = item.get("payload")
                actual = payload_sha256(payload) if isinstance(payload, bytes) else None
            elif item.get("object_kind", "").startswith("asset_"):
                data = item["asset_data"]
                actual = AssetPayload(
                    data["asset_id"], data["title"], data["body"], data["source_id"], data["source_role"]
                ).fingerprint()
            elif item.get("object_kind") == "exception":
                data = item["exception_data"]
                source_refs = tuple(BackendObjectRef(**ref_data) for ref_data in data.get("source_refs", []))
                actual = ExceptionRecord(
                    data["exception_id"], data["source_id"], data["reason_code"], data["safe_note"], data["question"], source_refs
                ).fingerprint()
            elif item.get("object_kind") == "logical_root":
                actual = root_payload_fingerprint(binding, item["logical_key"], item.get("content", ""))
            else:
                return False
        except (KeyError, TypeError, ValueError):
            return False
        return actual == expected


MappingLike = dict[str, Any]
