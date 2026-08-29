"""Durable two-confirmation contract for formal ZSK publishing."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Protocol, Sequence

from .contracts import CLIENT_ID, SOURCE_ID, MediaArtifact


APPROVAL_TYPE = "zsk_publish_approval_v1"
SESSION_TYPE = "zsk_content_confirmation_v1"
DESTINATIONS = frozenset({"03", "04", "05"})


class ApprovalIO(Protocol):
    def read(self, path: Path) -> bytes | None: ...
    def write_atomic(self, path: Path, payload: bytes) -> None: ...


class FileApprovalIO:
    def read(self, path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def write_atomic(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


@dataclass(frozen=True)
class StoredApproval:
    path: Path
    sha256: str
    reused: bool


class ApprovalStore:
    """Persist confirmation transitions with compare-and-swap protection."""

    def __init__(self, root: Path, *, io: ApprovalIO | None = None) -> None:
        self.root = root
        self.io = io or FileApprovalIO()

    def _path(self, source_id: str) -> Path:
        if not SOURCE_ID.fullmatch(source_id):
            raise ValueError("publish_approval_invalid")
        return self.root / f"{source_id}.json"

    def save(self, record: dict, *, expected_sha256: str | None = None) -> StoredApproval:
        if not isinstance(record, dict) or record.get("type") not in {SESSION_TYPE, APPROVAL_TYPE}:
            raise ValueError("publish_approval_invalid")
        path = self._path(str(record.get("source_id", "")))
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        digest = _sha256(payload)
        current = self.io.read(path)
        if expected_sha256 is None:
            if current is not None:
                if current == payload:
                    return StoredApproval(path, digest, True)
                raise ValueError("confirmation_record_conflict")
        else:
            if current is None or _sha256(current) != expected_sha256:
                raise ValueError("confirmation_record_conflict")
            if current == payload:
                return StoredApproval(path, digest, True)
        self.io.write_atomic(path, payload)
        return StoredApproval(path, digest, False)

    def load(self, source_id: str) -> dict:
        raw = self.io.read(self._path(source_id))
        if raw is None:
            raise ValueError("confirmation_record_missing")
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("publish_approval_invalid") from exc
        if not isinstance(record, dict) or record.get("source_id") != source_id:
            raise ValueError("publish_approval_invalid")
        return record


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _media_manifest_sha256(media: Sequence[MediaArtifact]) -> str:
    ordered = sorted(media, key=lambda item: item.page_number)
    pages = [item.page_number for item in ordered]
    if pages and pages != list(range(1, len(pages) + 1)):
        raise ValueError("media_confirmation_conflict")
    canonical = json.dumps([item.as_dict() for item in ordered], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256(canonical.encode("utf-8"))


def create_confirmation_session(
    source_id: str,
    client_id: str,
    proposed_destination: str,
    basis: str,
    media: Sequence[MediaArtifact],
) -> dict:
    if not SOURCE_ID.fullmatch(source_id) or not CLIENT_ID.fullmatch(client_id) or proposed_destination not in DESTINATIONS:
        raise ValueError("publish_approval_invalid")
    if not isinstance(basis, str) or not basis.strip():
        raise ValueError("classification_confirmation_required")
    return {
        "type": SESSION_TYPE,
        "state": "awaiting_classification",
        "source_id": source_id,
        "client_id": client_id,
        "classification": {"proposed_destination": proposed_destination, "basis": basis.strip(), "confirmed": False, "destination": None},
        "draft": {"attached": False, "asset_spec_sha256": None, "confirmed": False},
        "media_manifest_sha256": _media_manifest_sha256(media),
    }


def _session_copy(session: dict) -> dict:
    if not isinstance(session, dict) or session.get("type") != SESSION_TYPE:
        raise ValueError("publish_approval_invalid")
    return json.loads(json.dumps(session, ensure_ascii=False))


def confirm_classification(session: dict, destination: str) -> dict:
    updated = _session_copy(session)
    if updated.get("state") != "awaiting_classification":
        raise ValueError("classification_confirmation_required")
    classification = updated.get("classification")
    if not isinstance(classification, dict) or destination != classification.get("proposed_destination"):
        raise ValueError("classification_confirmation_conflict")
    classification.update({"confirmed": True, "destination": destination})
    updated["state"] = "classification_confirmed"
    return updated


def attach_draft(session: dict, draft: bytes) -> dict:
    updated = _session_copy(session)
    classification = updated.get("classification")
    if updated.get("state") != "classification_confirmed" or not isinstance(classification, dict) or classification.get("confirmed") is not True:
        raise ValueError("classification_confirmation_required")
    if not isinstance(draft, bytes) or not draft:
        raise ValueError("draft_confirmation_required")
    updated["draft"] = {"attached": True, "asset_spec_sha256": _sha256(draft), "confirmed": False}
    updated["state"] = "awaiting_draft_confirmation"
    return updated


def confirm_draft(session: dict, draft: bytes, media: Sequence[MediaArtifact]) -> dict:
    updated = _session_copy(session)
    if updated.get("state") != "awaiting_draft_confirmation":
        raise ValueError("draft_confirmation_required")
    draft_state = updated.get("draft")
    classification = updated.get("classification")
    if not isinstance(draft_state, dict) or draft_state.get("attached") is not True:
        raise ValueError("draft_confirmation_required")
    if draft_state.get("asset_spec_sha256") != _sha256(draft):
        raise ValueError("draft_confirmation_conflict")
    if updated.get("media_manifest_sha256") != _media_manifest_sha256(media):
        raise ValueError("media_confirmation_conflict")
    if not isinstance(classification, dict) or classification.get("confirmed") is not True:
        raise ValueError("classification_confirmation_required")
    return issue_publish_approval(
        updated["source_id"], updated["client_id"], classification["destination"], draft, media,
        classification_confirmed=True, draft_confirmed=True,
    )


def issue_publish_approval(
    source_id: str,
    client_id: str,
    route_destination: str,
    approved_draft: bytes,
    media: Sequence[MediaArtifact],
    *,
    classification_confirmed: bool,
    draft_confirmed: bool,
    confirmed_destination: str | None = None,
) -> dict:
    if not SOURCE_ID.fullmatch(source_id) or not CLIENT_ID.fullmatch(client_id):
        raise ValueError("publish_approval_invalid")
    if route_destination not in DESTINATIONS:
        raise ValueError("classification_confirmation_conflict")
    if classification_confirmed is not True:
        raise ValueError("classification_confirmation_required")
    destination = confirmed_destination or route_destination
    if destination != route_destination:
        raise ValueError("classification_confirmation_conflict")
    if draft_confirmed is not True:
        raise ValueError("draft_confirmation_required")
    return {
        "type": APPROVAL_TYPE,
        "source_id": source_id,
        "client_id": client_id,
        "classification_confirmation": {"confirmed": True, "destination": destination},
        "draft_confirmation": {
            "confirmed": True,
            "asset_spec_sha256": _sha256(approved_draft),
            "media_manifest_sha256": _media_manifest_sha256(media),
        },
    }


def verify_publish_approval(
    approval: dict,
    source_id: str,
    client_id: str,
    destination: str,
    draft: bytes,
    media: Sequence[MediaArtifact],
) -> dict:
    expected_keys = {"type", "source_id", "client_id", "classification_confirmation", "draft_confirmation"}
    if not isinstance(approval, dict) or set(approval) != expected_keys or approval.get("type") != APPROVAL_TYPE:
        raise ValueError("publish_approval_invalid")
    if approval.get("source_id") != source_id or approval.get("client_id") != client_id:
        raise ValueError("publish_approval_invalid")
    classification = approval.get("classification_confirmation")
    if not isinstance(classification, dict) or set(classification) != {"confirmed", "destination"}:
        raise ValueError("classification_confirmation_required")
    if classification.get("confirmed") is not True:
        raise ValueError("classification_confirmation_required")
    if classification.get("destination") != destination:
        raise ValueError("classification_confirmation_conflict")
    confirmed_draft = approval.get("draft_confirmation")
    if not isinstance(confirmed_draft, dict) or set(confirmed_draft) != {"confirmed", "asset_spec_sha256", "media_manifest_sha256"}:
        raise ValueError("draft_confirmation_required")
    if confirmed_draft.get("confirmed") is not True:
        raise ValueError("draft_confirmation_required")
    if confirmed_draft.get("asset_spec_sha256") != _sha256(draft):
        raise ValueError("draft_confirmation_conflict")
    if confirmed_draft.get("media_manifest_sha256") != _media_manifest_sha256(media):
        raise ValueError("media_confirmation_conflict")
    return approval
