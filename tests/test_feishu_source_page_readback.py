from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import SOURCE_SCHEMA, PageArtifact, PageTextEvidence, SourceRecord  # noqa: E402
from shared.feishu_cli import RecordedCliCall, RecordedCliRunner  # noqa: E402
from shared.feishu_stage5 import FeishuStage5Storage  # noqa: E402


PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x06@\x00\x00\x03\x84remote-page"
SOURCE_ID = "SRC-" + hashlib.sha256(b"deck").hexdigest()[:24]
PAGE_SHA = hashlib.sha256(PNG).hexdigest()


def source() -> tuple[SourceRecord, PageArtifact]:
    page = PageArtifact(f"{SOURCE_ID}-PAGE-001", SOURCE_ID, 1, "page-001.png", PAGE_SHA, 1600, 900)
    text = PageTextEvidence.create(
        SOURCE_ID,
        1,
        PAGE_SHA,
        native_text="",
        ocr_text="原始识别",
        verbatim_text="校对正文",
        text_source="ocr",
        confidence=0.72,
        review_status="approved",
    )
    record = SourceRecord(
        SOURCE_SCHEMA,
        SOURCE_ID,
        "CLT-1234567890ABCD",
        "资料",
        "business_knowledge",
        "presentation",
        "资料.pptx",
        hashlib.sha256(b"deck").hexdigest(),
        hashlib.sha256(b"readable").hexdigest(),
        "passed",
        "allowed",
        None,
        "registered",
        True,
        page_evidence_mode="required",
        page_count=1,
        page_artifacts=(page,),
        page_text_evidence=(text,),
        display_name="资料",
    )
    return record, page


def calls(downloaded: bytes) -> tuple[RecordedCliCall, ...]:
    record, page = source()
    list_call = (
        "lark-cli", "--as", "user", "wiki", "nodes", "list", "--space-id", "1",
        "--parent-node-token", "root-01", "--page-all", "--format", "json",
    )
    fetch_markdown = (
        "lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc",
        "readable-token", "--doc-format", "markdown", "--format", "json",
    )
    caption = f"第 1 页｜SHA256 {page.sha256}"
    media_insert = (
        "lark-cli", "--as", "user", "docs", "+media-insert", "--doc", "readable-token",
        "--file", "{file}", "--width", "1600", "--height", "900", "--align", "center",
        "--caption", caption, "--format", "json",
    )
    fetch_full = (
        "lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc",
        "readable-token", "--doc-format", "xml", "--detail", "full", "--format", "json",
    )
    download = (
        "lark-cli", "--as", "user", "docs", "+media-download", "--token", "media-token",
        "--output", "{output}", "--format", "json",
    )
    readable = f'---\nsource_id: "{record.source_id}"\ndisplay_name: "资料"\noriginal_file_name: "资料.pptx"\n---\n'
    xml = f'<title>资料</title><p>校对正文</p><img src="media-token" width="1600" height="900" caption="{caption}&#xA;"/>'
    return (
        RecordedCliCall(list_call, '{"ok":true,"data":{"items":[{"title":"资料","obj_type":"docx","obj_token":"readable-token"}]}}'),
        RecordedCliCall(fetch_markdown, json.dumps({"ok": True, "data": {"document": {"content": readable}}}, ensure_ascii=False)),
        RecordedCliCall(fetch_full, json.dumps({"ok": True, "data": {"document": {"content": "<title>资料</title><p>校对正文</p>", "reference_map": {}}}}, ensure_ascii=False)),
        RecordedCliCall(
            media_insert,
            '{"ok":true,"data":{"document_id":"readable-token","block_id":"blk","file_token":"media-token"}}',
            payload=PNG,
            upload_name="page-001.png",
        ),
        RecordedCliCall(fetch_full, json.dumps({"ok": True, "data": {"document": {"content": xml, "reference_map": {}}}}, ensure_ascii=False)),
        RecordedCliCall(download, '{"ok":true,"data":{"output":"page-001.png"}}', download_payload=downloaded, download_name="page-001.png"),
    )


class FeishuSourcePageReadbackTests(unittest.TestCase):
    def test_readable_body_tampering_is_not_hidden_by_unchanged_hash_markers(self) -> None:
        payload = "## 页级校对正文\n\n校对正文".encode("utf-8")
        stored = FeishuStage5Storage._document("资料", payload)
        self.assertTrue(FeishuStage5Storage._matches_document("资料", payload, stored))
        tampered = stored.replace("校对正文", "被篡改正文")
        self.assertFalse(FeishuStage5Storage._matches_document("资料", payload, tampered))

    def test_readable_body_reordering_is_detected(self) -> None:
        payload = "甲方 支付 乙方".encode("utf-8")
        stored = FeishuStage5Storage._document("资料", payload)
        tampered = stored.replace("甲方 支付 乙方", "乙方 支付 甲方")
        self.assertFalse(FeishuStage5Storage._matches_document("资料", payload, tampered))

    def test_readable_xml_with_element_ids_is_verified_semantically(self) -> None:
        payload = "## 页级校对正文\n\n校对正文".encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        stored = (
            '<title id="doc-token">资料</title>'
            f'<p id="first">内容校验：<code>{digest}</code></p>'
            '<h2 id="heading">页级校对正文</h2><p id="body">校对正文</p>'
            f'<p id="last">校验结束：<code>{digest}</code></p>'
        )
        self.assertTrue(FeishuStage5Storage._matches_document("资料", payload, stored))
        self.assertFalse(FeishuStage5Storage._matches_document("资料", payload, stored.replace("校对正文", "被篡改正文")))

    def test_readable_markdown_normalization_does_not_create_version_conflict(self) -> None:
        payload = "---\nsource_id: \"SRC-1\"\n---\n\n| 户型 | 单价 |\n| --- | --- |\n| 160 | 12.8 |".encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        stored = (
            "# 资料\n\n"
            f"内容校验：`{digest}`\n\n"
            "---\n\n## source_id: \"SRC-1\"  \n\n| 户型 | 单价 |\n|-|-|\n| 160 | 12.8 |\n\n"
            f"校验结束：`{digest}`"
        )
        self.assertTrue(FeishuStage5Storage._matches_document("资料", payload, stored))
        self.assertFalse(FeishuStage5Storage._matches_document("资料", payload, stored.replace("12.8", "99.9")))

    def test_unwrapped_asset_allows_table_normalization_but_not_tampering(self) -> None:
        payload = "# 资料\n\n| 户型 | 单价 |\n| --- | --- |\n| 160 | 12.8 |".encode("utf-8")
        stored = "# 资料\n\n| 户型 | 单价 |\n|-|-|\n| 160 | 12.8 |"
        self.assertTrue(FeishuStage5Storage._matches_document("资料", payload, stored, wrapped=False))
        self.assertFalse(FeishuStage5Storage._matches_document("资料", payload, stored.replace("12.8", "99.9"), wrapped=False))

    def test_embeds_page_and_reads_back_count_dimensions_and_hash(self) -> None:
        record, page = source()
        runner = RecordedCliRunner(calls(PNG))
        result = FeishuStage5Storage(runner, "1", {"01": "root-01", "02": "root-02"}).store_page_evidence(record, page, PNG)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.metadata["image_count"], 1)
        self.assertEqual(result.metadata["width_px"], 1600)
        self.assertEqual(result.metadata["height_px"], 900)
        self.assertEqual(result.metadata["sha256"], PAGE_SHA)
        self.assertIn("media_sha256_readback", result.checked)
        self.assertTrue(runner.exhausted)

    def test_cached_readable_token_avoids_rescanning_all_source_documents_per_page(self) -> None:
        record, page = source()
        runner = RecordedCliRunner(calls(PNG)[2:])
        storage = FeishuStage5Storage(runner, "1", {"01": "root-01", "02": "root-02"})
        storage._doc_tokens[f"{record.source_id}:readable"] = "readable-token"
        result = storage.store_page_evidence(record, page, PNG)
        self.assertEqual(result.status, "ok")
        self.assertTrue(runner.exhausted)

    def test_remote_media_hash_mismatch_fails_closed(self) -> None:
        record, page = source()
        runner = RecordedCliRunner(calls(b"tampered"))
        result = FeishuStage5Storage(runner, "1", {"01": "root-01", "02": "root-02"}).store_page_evidence(record, page, PNG)
        self.assertEqual((result.status, result.code), ("blocked", "readback_failed"))
        self.assertTrue(runner.exhausted)


if __name__ == "__main__":
    unittest.main()
