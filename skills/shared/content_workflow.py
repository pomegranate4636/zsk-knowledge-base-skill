"""Router-facing, durable two-confirmation workflow for content publishing."""
from __future__ import annotations

from dataclasses import dataclass

from .approval import (
    ApprovalStore,
    StoredApproval,
    attach_draft,
    confirm_classification,
    confirm_draft,
    create_confirmation_session,
)
from .contracts import Binding, SourceRecord


ROLE_DESTINATION = {
    "business_knowledge": "03",
    "reference_method": "04",
    "profile_material": "05",
}


@dataclass(frozen=True)
class WorkflowRecord:
    record: dict
    stored: StoredApproval

    @property
    def sha256(self) -> str:
        return self.stored.sha256

    @property
    def path(self):
        return self.stored.path


class ContentWorkflow:
    def __init__(self, store: ApprovalStore) -> None:
        self.store = store

    @staticmethod
    def _source_gate(binding: Binding, source: SourceRecord, destination: str | None = None) -> None:
        if source.client_id != binding.client_id:
            raise ValueError("binding_conflict")
        if source.status not in {"registered", "reused"} or source.permission_status != "allowed" or source.privacy_status not in {"passed", "redacted"}:
            raise ValueError("source_not_dispatchable")
        expected = ROLE_DESTINATION.get(source.source_role)
        if expected is None or destination is not None and destination != expected:
            raise ValueError("classification_confirmation_conflict")
        if source.content_kind in {"document", "presentation"} and source.original_name.lower().endswith((".pdf", ".pptx")):
            pages = [item.page_number for item in source.media_artifacts]
            if source.page_count < 1 or pages != list(range(1, source.page_count + 1)) or source.visual_processing_status != "ocr_completed":
                raise ValueError("media_confirmation_conflict")

    def begin(self, binding: Binding, source: SourceRecord, destination: str, basis: str) -> WorkflowRecord:
        self._source_gate(binding, source, destination)
        record = create_confirmation_session(source.source_id, binding.client_id, destination, basis, source.media_artifacts)
        return WorkflowRecord(record, self.store.save(record))

    def confirm_destination(self, source_id: str, destination: str, *, expected_sha256: str) -> WorkflowRecord:
        record = confirm_classification(self.store.load(source_id), destination)
        return WorkflowRecord(record, self.store.save(record, expected_sha256=expected_sha256))

    def present_draft(self, source_id: str, draft: bytes, *, expected_sha256: str) -> WorkflowRecord:
        record = attach_draft(self.store.load(source_id), draft)
        return WorkflowRecord(record, self.store.save(record, expected_sha256=expected_sha256))

    def confirm_publish(self, source: SourceRecord, draft: bytes, *, expected_sha256: str) -> WorkflowRecord:
        expected_destination = ROLE_DESTINATION.get(source.source_role)
        if expected_destination is None:
            raise ValueError("classification_confirmation_conflict")
        record = confirm_draft(self.store.load(source.source_id), draft, source.media_artifacts)
        if record["classification_confirmation"]["destination"] != expected_destination:
            raise ValueError("classification_confirmation_conflict")
        return WorkflowRecord(record, self.store.save(record, expected_sha256=expected_sha256))
