from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import MediaArtifact  # noqa: E402
from shared.feishu_cli import RecordedCliCall, RecordedCliRunner  # noqa: E402
from shared.feishu_stage5 import FeishuStage5Storage  # noqa: E402


class FeishuMediaPublishTests(unittest.TestCase):
    def test_media_insert_checks_marker_then_inserts_and_reads_back(self) -> None:
        payload = b"page-image"
        source_id = "SRC-" + "a" * 24
        media = MediaArtifact(
            f"{source_id}-PAGE-001", source_id, 1, "image", "page-001.png", hashlib.sha256(payload).hexdigest(),
        )
        marker = f"ZSK:{source_id}:P001:{media.sha256[:16]}"
        fetch = ("lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", "doc-token", "--doc-format", "markdown", "--detail", "with-ids", "--format", "json")
        insert = ("lark-cli", "--as", "user", "docs", "+media-insert", "--doc", "doc-token", "--file", "{file}", "--caption", marker, "--align", "center", "--format", "json")
        runner = RecordedCliRunner((
            RecordedCliCall(fetch, '{"ok":true,"data":{"document":{"content":"# 正文"}}}'),
            RecordedCliCall(insert, '{"ok":true,"data":{"block_id":"image-block"}}', payload=payload, upload_name="page-001.png"),
            RecordedCliCall(fetch, '{"ok":true,"data":{"document":{"content":"# 正文\\n' + marker + '"}}}'),
        ))
        storage = FeishuStage5Storage(runner, "1", {"01": "root-01"})

        result = storage.insert_document_media("doc-token", source_id, ((media, payload),))

        self.assertEqual(result.status, "ok")
        self.assertIn("image_marker_readback", result.checked)
        self.assertTrue(runner.exhausted)

    def test_existing_marker_skips_duplicate_image_insert(self) -> None:
        payload = b"page-image"
        source_id = "SRC-" + "a" * 24
        media = MediaArtifact(f"{source_id}-PAGE-001", source_id, 1, "image", "page-001.png", hashlib.sha256(payload).hexdigest())
        marker = f"ZSK:{source_id}:P001:{media.sha256[:16]}"
        fetch = ("lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", "doc-token", "--doc-format", "markdown", "--detail", "with-ids", "--format", "json")
        runner = RecordedCliRunner((RecordedCliCall(fetch, '{"ok":true,"data":{"document":{"content":"' + marker + '"}}}'),))
        storage = FeishuStage5Storage(runner, "1", {"01": "root-01"})

        result = storage.insert_document_media("doc-token", source_id, ((media, payload),))

        self.assertEqual(result.status, "reused")
        self.assertTrue(runner.exhausted)


if __name__ == "__main__":
    unittest.main()
