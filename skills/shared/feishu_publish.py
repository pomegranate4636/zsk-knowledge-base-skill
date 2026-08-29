"""Approved, resumable Feishu publishing transaction.

The workflow validates the durable two-confirmation record before the first
backend call, stores a receipt after every irreversible phase, and verifies
both documents after page-image insertion.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping, Protocol

from .approval import ApprovalIO, FileApprovalIO, verify_publish_approval
from .contracts import AdapterResult, AssetPayload, BackendObjectRef, Binding, SourceRecord


RECEIPT_TYPE = "zsk_feishu_publish_receipt_v1"


class FeishuPublishBackend(Protocol):
    def doctor(self, binding: Binding) -> AdapterResult: ...
    def publish_source(self, binding: Binding, source: SourceRecord, readable: bytes, media: Mapping[int, bytes]) -> AdapterResult: ...
    def publish_asset(self, binding: Binding, source: SourceRecord, asset: AssetPayload, destination: str, media: Mapping[int, bytes]) -> AdapterResult: ...
    def read_back(self, binding: Binding, refs: tuple[BackendObjectRef, ...]) -> AdapterResult: ...


@dataclass(frozen=True)
class FeishuPublishRequest:
    binding: Binding
    source: SourceRecord
    asset: AssetPayload
    destination: str
    draft: bytes
    readable_payload: bytes
    media_payloads: Mapping[int, bytes]
    approval: dict


@dataclass(frozen=True)
class PublishResult:
    record: dict
    path: Path


class PublishReceiptStore:
    def __init__(self, root: Path, *, io: ApprovalIO | None = None) -> None:
        self.root = root
        self.io = io or FileApprovalIO()

    def path_for(self, source_id: str, asset_sha256: str) -> Path:
        return self.root / f"{source_id}-{asset_sha256[:20]}.json"

    def load(self, path: Path) -> dict | None:
        raw = self.io.read(path)
        if raw is None:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("feishu_publish_receipt_invalid") from exc
        if not isinstance(value, dict) or value.get("type") != RECEIPT_TYPE:
            raise ValueError("feishu_publish_receipt_invalid")
        return value

    def save(self, path: Path, record: dict) -> None:
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self.io.write_atomic(path, payload)


class FeishuPublishWorkflow:
    def __init__(self, backend: FeishuPublishBackend, receipts: PublishReceiptStore) -> None:
        self.backend = backend
        self.receipts = receipts

    def publish(self, request: FeishuPublishRequest) -> PublishResult:
        self._validate_request(request)
        verify_publish_approval(
            request.approval,
            request.source.source_id,
            request.binding.client_id,
            request.destination,
            request.draft,
            request.source.media_artifacts,
        )
        asset_sha = hashlib.sha256(request.draft).hexdigest()
        path = self.receipts.path_for(request.source.source_id, asset_sha)
        record = self.receipts.load(path) or self._new_receipt(request, asset_sha)
        self._verify_receipt(record, request, asset_sha)
        if record["status"] == "published":
            return PublishResult(record, path)
        record["attempts"] += 1
        record["last_error"] = None

        doctor = self.backend.doctor(request.binding)
        if doctor.status not in {"ok", "reused"}:
            return self._stop(path, record, doctor)

        if record["source_document_url"] is None:
            source_result = self.backend.publish_source(request.binding, request.source, request.readable_payload, request.media_payloads)
            if source_result.status not in {"ok", "reused"}:
                return self._stop(path, record, source_result)
            source_url = source_result.metadata.get("document_url")
            if not isinstance(source_url, str) or not source_url.startswith("https://") or not source_result.object_refs:
                return self._stop(path, record, AdapterResult.failed("readback_failed", "Source publish returned no stable link.", blocked=True))
            record["source_document_url"] = source_url
            record["source_refs"] = [ref.as_dict() for ref in source_result.object_refs]
            self.receipts.save(path, record)

        if record["knowledge_document_url"] is None:
            asset_result = self.backend.publish_asset(request.binding, request.source, request.asset, request.destination, request.media_payloads)
            if asset_result.status not in {"ok", "reused"}:
                return self._stop(path, record, asset_result)
            asset_url = asset_result.metadata.get("document_url")
            if not isinstance(asset_url, str) or not asset_url.startswith("https://") or not asset_result.object_refs:
                return self._stop(path, record, AdapterResult.failed("readback_failed", "Asset publish returned no stable link.", blocked=True))
            record["knowledge_document_url"] = asset_url
            record["asset_refs"] = [ref.as_dict() for ref in asset_result.object_refs]
            self.receipts.save(path, record)

        refs = tuple(self._ref(value) for value in (*record["source_refs"], *record["asset_refs"]))
        readback = self.backend.read_back(request.binding, refs)
        if readback.status not in {"ok", "reused"}:
            return self._stop(path, record, readback)
        required = {ref.object_id for ref in refs}
        if {ref.object_id for ref in readback.object_refs} != required:
            return self._stop(path, record, AdapterResult.failed("readback_failed", "Final readback refs differ.", blocked=True))
        record.update({"status": "published", "last_error": None, "readback": {"status": "verified", "checked": list(readback.checked)}})
        self.receipts.save(path, record)
        return PublishResult(record, path)

    def _stop(self, path: Path, record: dict, result: AdapterResult) -> PublishResult:
        record["status"] = "blocked" if result.status == "blocked" else "pending"
        record["last_error"] = result.code or "write_failed"
        self.receipts.save(path, record)
        return PublishResult(record, path)

    @staticmethod
    def _validate_request(request: FeishuPublishRequest) -> None:
        if request.binding.backend_type != "feishu" or request.source.client_id != request.binding.client_id:
            raise ValueError("binding_conflict")
        if request.destination not in {"03", "04", "05"} or request.asset.source_id != request.source.source_id:
            raise ValueError("classification_confirmation_conflict")
        if request.asset.body.encode("utf-8") != request.draft:
            raise ValueError("draft_confirmation_conflict")
        if hashlib.sha256(request.readable_payload).hexdigest() != request.source.readable_sha256:
            raise ValueError("source_readback_mismatch")
        expected = {item.page_number: item.sha256 for item in request.source.media_artifacts}
        actual = {page: hashlib.sha256(payload).hexdigest() for page, payload in request.media_payloads.items()}
        if actual != expected:
            raise ValueError("media_confirmation_conflict")
        referenced = request.asset.metadata.get("evidence_pages", [])
        if not isinstance(referenced, list) or any(page not in expected for page in referenced):
            raise ValueError("media_confirmation_conflict")

    @staticmethod
    def _new_receipt(request: FeishuPublishRequest, asset_sha: str) -> dict:
        return {
            "type": RECEIPT_TYPE,
            "status": "pending",
            "source_id": request.source.source_id,
            "client_id": request.binding.client_id,
            "destination": request.destination,
            "asset_sha256": asset_sha,
            "readable_sha256": request.source.readable_sha256,
            "media_manifest_sha256": request.approval["draft_confirmation"]["media_manifest_sha256"],
            "attempts": 0,
            "last_error": None,
            "source_document_url": None,
            "knowledge_document_url": None,
            "source_refs": [],
            "asset_refs": [],
            "readback": {"status": "pending", "checked": []},
        }

    @staticmethod
    def _verify_receipt(record: dict, request: FeishuPublishRequest, asset_sha: str) -> None:
        expected = {
            "source_id": request.source.source_id,
            "client_id": request.binding.client_id,
            "destination": request.destination,
            "asset_sha256": asset_sha,
            "readable_sha256": request.source.readable_sha256,
            "media_manifest_sha256": request.approval["draft_confirmation"]["media_manifest_sha256"],
        }
        if record.get("type") != RECEIPT_TYPE or any(record.get(key) != value for key, value in expected.items()):
            raise ValueError("feishu_publish_receipt_conflict")
        if record.get("status") not in {"pending", "blocked", "published"}:
            raise ValueError("feishu_publish_receipt_invalid")

    @staticmethod
    def _ref(value: dict) -> BackendObjectRef:
        try:
            return BackendObjectRef(value["object_id"], value["object_kind"], value["locator"], value["version"])
        except (KeyError, TypeError) as exc:
            raise ValueError("feishu_publish_receipt_invalid") from exc


class FeishuAdapterPublishBackend:
    """Expose a resolved FeishuAdapter through the formal publish protocol."""

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def doctor(self, binding: Binding) -> AdapterResult:
        checked = self.adapter.doctor()
        if checked.status not in {"ok", "reused"}:
            return checked
        resolved = self.adapter.resolve_binding(binding)
        if resolved.status not in {"ok", "reused"}:
            return resolved
        structure = self.adapter.inspect_structure(binding)
        if structure.status != "reused":
            return structure if structure.status in {"blocked", "failed"} else AdapterResult.failed("structure_conflict", "Formal publish requires a complete knowledge base.", blocked=True)
        return AdapterResult.ok(checked=("feishu_auth", "required_scopes", "binding", "complete_structure"))

    def publish_source(self, binding: Binding, source: SourceRecord, readable: bytes, media: Mapping[int, bytes]) -> AdapterResult:
        return self.adapter.publish_source_content(binding, source, readable, media)

    def publish_asset(self, binding: Binding, source: SourceRecord, asset: AssetPayload, destination: str, media: Mapping[int, bytes]) -> AdapterResult:
        return self.adapter.publish_approved_asset(binding, source, asset, destination, media)

    def read_back(self, binding: Binding, refs: tuple[BackendObjectRef, ...]) -> AdapterResult:
        return self.adapter.read_back(binding, refs)
