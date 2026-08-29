from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.approval import (  # noqa: E402
    ApprovalStore,
    attach_draft,
    confirm_classification,
    confirm_draft,
    create_confirmation_session,
    issue_publish_approval,
    verify_publish_approval,
)
from shared.contracts import MediaArtifact  # noqa: E402


SOURCE_ID = "SRC-" + "a" * 24


def media(payload: bytes = b"image") -> tuple[MediaArtifact, ...]:
    return (MediaArtifact(f"{SOURCE_ID}-PAGE-001", SOURCE_ID, 1, "image", "page-001.png", hashlib.sha256(payload).hexdigest()),)


class PublishApprovalTests(unittest.TestCase):
    def test_persistent_store_uses_compare_and_swap_between_turns(self) -> None:
        class MemoryIO:
            def __init__(self) -> None:
                self.data = {}

            def read(self, path):
                return self.data.get(str(path))

            def write_atomic(self, path, payload):
                self.data[str(path)] = payload

        io = MemoryIO()
        store = ApprovalStore(Path("C:/zsk-runtime/approvals"), io=io)
        first = create_confirmation_session(SOURCE_ID, "CLT-123", "03", "正文依据", media())
        first_saved = store.save(first)
        classified = confirm_classification(first, "03")
        classified_saved = store.save(classified, expected_sha256=first_saved.sha256)

        self.assertEqual(store.load(SOURCE_ID)["state"], "classification_confirmed")
        self.assertNotEqual(first_saved.sha256, classified_saved.sha256)
        with self.assertRaisesRegex(ValueError, "confirmation_record_conflict"):
            store.save(attach_draft(classified, b"draft"), expected_sha256=first_saved.sha256)

    def test_confirmation_session_enforces_two_distinct_turns(self) -> None:
        session = create_confirmation_session(SOURCE_ID, "CLT-123", "03", "正文是客户项目事实", media())
        self.assertEqual(session["state"], "awaiting_classification")
        with self.assertRaisesRegex(ValueError, "classification_confirmation_required"):
            attach_draft(session, b"draft")

        classified = confirm_classification(session, "03")
        drafted = attach_draft(classified, b"draft")
        self.assertEqual(drafted["state"], "awaiting_draft_confirmation")
        approval = confirm_draft(drafted, b"draft", media())
        self.assertEqual(approval["type"], "zsk_publish_approval_v1")

    def test_confirmation_session_rejects_changed_media_before_final_confirmation(self) -> None:
        session = create_confirmation_session(SOURCE_ID, "CLT-123", "03", "正文依据", media())
        drafted = attach_draft(confirm_classification(session, "03"), b"draft")
        with self.assertRaisesRegex(ValueError, "media_confirmation_conflict"):
            confirm_draft(drafted, b"draft", media(b"changed"))

    def test_classification_confirmation_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "classification_confirmation_required"):
            issue_publish_approval(SOURCE_ID, "CLT-123", "03", b"draft", media(), classification_confirmed=False, draft_confirmed=True)

    def test_confirmed_destination_must_match_route(self) -> None:
        with self.assertRaisesRegex(ValueError, "classification_confirmation_conflict"):
            issue_publish_approval(SOURCE_ID, "CLT-123", "03", b"draft", media(), classification_confirmed=True, draft_confirmed=True, confirmed_destination="04")

    def test_changed_draft_invalidates_approval(self) -> None:
        approval = issue_publish_approval(SOURCE_ID, "CLT-123", "03", b"draft", media(), classification_confirmed=True, draft_confirmed=True)
        with self.assertRaisesRegex(ValueError, "draft_confirmation_conflict"):
            verify_publish_approval(approval, SOURCE_ID, "CLT-123", "03", b"changed", media())

    def test_changed_media_manifest_invalidates_approval(self) -> None:
        approval = issue_publish_approval(SOURCE_ID, "CLT-123", "03", b"draft", media(), classification_confirmed=True, draft_confirmed=True)
        with self.assertRaisesRegex(ValueError, "media_confirmation_conflict"):
            verify_publish_approval(approval, SOURCE_ID, "CLT-123", "03", b"draft", media(b"changed"))

    def test_valid_approval_binds_both_confirmations(self) -> None:
        approval = issue_publish_approval(SOURCE_ID, "CLT-123", "03", b"draft", media(), classification_confirmed=True, draft_confirmed=True)
        verified = verify_publish_approval(approval, SOURCE_ID, "CLT-123", "03", b"draft", media())
        self.assertEqual(verified["type"], "zsk_publish_approval_v1")
        self.assertTrue(verified["classification_confirmation"]["confirmed"])
        self.assertTrue(verified["draft_confirmation"]["confirmed"])


if __name__ == "__main__":
    unittest.main()
