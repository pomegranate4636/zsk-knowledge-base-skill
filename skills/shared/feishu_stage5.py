"""飞书阶段 5 的薄 File/Doc 存储器；对象 token 不向上层暴露。"""
from __future__ import annotations
import hashlib
import json
from typing import Any, Mapping, Sequence
from .contracts import AdapterResult, BackendObjectRef, ExceptionRecord, MediaArtifact, SourceRecord
from .feishu_cli import CliResponse, CliRunner
class FeishuStage5Storage:
    def __init__(self, runner: CliRunner, space_id: str, root_nodes: Mapping[str, str]) -> None:
        self.runner = runner
        self.space_id = space_id
        self.root_nodes = dict(root_nodes)
        self._stored: dict[str, tuple[str, BackendObjectRef]] = {}
        self._documents: dict[str, tuple[str, str]] = {}
        self._document_keys: dict[str, str] = {}
        self._document_expectations: dict[str, tuple[str, bytes, bool, tuple[str, ...]]] = {}
    def store_original(self, source: SourceRecord, payload: bytes) -> AdapterResult:
        if hashlib.sha256(payload).hexdigest() != source.original_sha256:
            return AdapterResult.failed("source_unreadable", "Original payload hash does not match its record.", blocked=True)
        key = f"{source.source_id}:original"
        replay = self._replay(key, payload)
        if replay:
            return replay
        node, failure = self._find(self.root_nodes["01"], f"{source.source_id}.bin", "file")
        if failure:
            return failure
        if node:
            ref = self._ref(key, "source_original", payload, "feishu://01/original")
            self._stored[key] = (source.original_sha256, ref)
            return AdapterResult.reused(ref, checked=("remote_file_present", "content_addressed_name", "stable_file_token"))
        argv = (
            "lark-cli", "--as", "user", "drive", "+upload", "--file", "{file}",
            "--wiki-token", self.root_nodes["01"], "--name", f"{source.source_id}.bin", "--format", "json",
        )
        data, failure = self._response(self.runner.upload(argv, payload=payload, name=f"{source.source_id}.bin"), "write_failed")
        if failure:
            return failure
        token = data.get("file_token") or data.get("token")
        if not isinstance(token, str) or not token:
            return AdapterResult.failed("readback_failed", "Uploaded File has no stable token.", blocked=True)
        ref = self._ref(key, "source_original", payload, "feishu://01/original")
        self._stored[key] = (hashlib.sha256(payload).hexdigest(), ref)
        return AdapterResult.ok(ref, checked=("drive_file_uploaded", "content_addressed_name", "stable_file_token"))
    def store_readable(self, source: SourceRecord, payload: bytes) -> AdapterResult:
        if hashlib.sha256(payload).hexdigest() != source.readable_sha256:
            return AdapterResult.failed("source_unreadable", "Readable payload hash does not match its record.", blocked=True)
        return self._store_doc(f"{source.source_id}:readable", source.source_id, self.root_nodes["01"], payload, "source_readable", "feishu://01/readable")
    def store_media(self, source: SourceRecord, media: MediaArtifact, payload: bytes) -> AdapterResult:
        if media.source_id != source.source_id or hashlib.sha256(payload).hexdigest() != media.sha256:
            return AdapterResult.failed("source_unreadable", "Media payload hash does not match its record.", blocked=True)
        key = f"{source.source_id}:media:{media.page_number:03d}"
        replay = self._replay(key, payload)
        if replay:
            return replay
        name = f"{source.source_id}-page-{media.page_number:03d}.png"
        node, failure = self._find(self.root_nodes["01"], name, "file")
        if failure:
            return failure
        if node:
            ref = self._ref(key, "source_media", payload, f"feishu://01/media/{media.page_number:03d}")
            self._stored[key] = (media.sha256, ref)
            return AdapterResult.reused(ref, checked=("remote_media_present", "page_number", "payload_sha256"))
        argv = ("lark-cli", "--as", "user", "drive", "+upload", "--file", "{file}", "--wiki-token", self.root_nodes["01"], "--name", name, "--format", "json")
        data, failure = self._response(self.runner.upload(argv, payload=payload, name=name), "write_failed")
        if failure:
            return failure
        if not isinstance(data.get("file_token") or data.get("token"), str):
            return AdapterResult.failed("readback_failed", "Uploaded media has no stable token.", blocked=True)
        ref = self._ref(key, "source_media", payload, f"feishu://01/media/{media.page_number:03d}")
        self._stored[key] = (media.sha256, ref)
        return AdapterResult.ok(ref, checked=("drive_media_uploaded", "page_number", "stable_file_token"))
    def write_exception(self, exception: ExceptionRecord) -> AdapterResult:
        payload = (f"原因码：`{exception.reason_code}`\n\n{exception.safe_note}\n\n待确认：{exception.question}\n").encode("utf-8")
        return self._store_doc(f"exception:{exception.exception_id}", exception.exception_id, self.root_nodes["02"], payload, "exception", "feishu://02")

    def store_document(self, key: str, title: str, parent: str, payload: bytes, kind: str, locator: str) -> AdapterResult:
        return self._store_doc(key, title, parent, payload, kind, locator, wrapped=False)

    def store_document_with_media(
        self,
        key: str,
        title: str,
        parent: str,
        payload: bytes,
        kind: str,
        locator: str,
        source_id: str,
        media_payloads: Sequence[tuple[MediaArtifact, bytes]],
        *,
        wrapped: bool = False,
    ) -> AdapterResult:
        stored = self._store_doc(key, title, parent, payload, kind, locator, wrapped=wrapped)
        if stored.status not in {"ok", "reused"}:
            return stored
        document = self._documents.get(key)
        if document is None:
            return AdapterResult.failed("readback_failed", "Published document identity is unavailable.", blocked=True)
        token, url = document
        inserted = self.insert_document_media(token, source_id, media_payloads)
        if inserted.status not in {"ok", "reused"}:
            return inserted
        status = "ok" if stored.status == "ok" or inserted.status == "ok" else "reused"
        expectation = self._document_expectations.get(key)
        if expectation is not None:
            title_value, payload_value, wrapped_value, _old_markers = expectation
            markers = tuple(self._media_marker(source_id, item) for item, _payload in media_payloads)
            self._document_expectations[key] = (title_value, payload_value, wrapped_value, markers)
        checked = tuple(dict.fromkeys((*stored.checked, *inserted.checked, "document_url")))
        factory = AdapterResult.ok if status == "ok" else AdapterResult.reused
        return factory(*stored.object_refs, checked=checked, metadata={"document_url": url})

    def verify_document_refs(self, refs: Sequence[BackendObjectRef]) -> AdapterResult:
        verified: list[BackendObjectRef] = []
        for ref in refs:
            key = self._document_keys.get(ref.object_id)
            if key is None:
                continue
            document = self._documents.get(key)
            expectation = self._document_expectations.get(key)
            if document is None or expectation is None:
                return AdapterResult.failed("readback_failed", "Document verification state is missing.", blocked=True)
            token, _url = document
            title, payload, wrapped, markers = expectation
            content, failure = self._fetch_detail(token)
            if failure:
                return failure
            if not self._matches_document(title, payload, content, wrapped) or any(marker not in content for marker in markers):
                return AdapterResult.failed("readback_failed", "Document content or image markers changed.", blocked=True)
            verified.append(ref)
        return AdapterResult.ok(*verified, checked=("document_content_sha256", "image_marker_readback", "stable_refs"))

    def registered_source(self, source_id: str, source_role: str) -> AdapterResult:
        original, failure = self._find(self.root_nodes["01"], f"{source_id}.bin", "file")
        readable, readable_failure = self._find(self.root_nodes["01"], source_id, "docx")
        if failure or readable_failure:
            return failure or readable_failure  # type: ignore[return-value]
        if not original or not readable:
            return AdapterResult.failed("ownership_unknown", "Asset source is not fully registered in 01.", blocked=True)
        fetch = ("lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", readable["obj_token"], "--doc-format", "markdown", "--format", "json")
        data, failure = self._response(self.runner.run(fetch), "readback_failed")
        content = data.get("document", {}).get("content", "") if isinstance(data.get("document"), dict) else ""
        if failure or source_id not in str(content):
            return failure or AdapterResult.failed("readback_failed", "Registered source readable copy cannot be verified.", blocked=True)
        if f'source_role: "{source_role}"' not in str(content):
            return AdapterResult.failed("routing_ambiguous", "Registered source role does not match this asset destination.", blocked=True)
        return AdapterResult.ok(checked=("source_original_present", "source_readable_present", "source_role_verified"))

    def registered_business_source(self, source_id: str) -> AdapterResult:
        return self.registered_source(source_id, "business_knowledge")

    def insert_document_media(self, document_token: str, source_id: str, media_payloads: Sequence[tuple[MediaArtifact, bytes]]) -> AdapterResult:
        """Insert content-addressed page images and prove each marker by readback."""
        if not isinstance(document_token, str) or not document_token:
            return AdapterResult.failed("readback_failed", "Document token is missing.", blocked=True)
        ordered = sorted(media_payloads, key=lambda pair: pair[0].page_number)
        if any(item.source_id != source_id or hashlib.sha256(payload).hexdigest() != item.sha256 for item, payload in ordered):
            return AdapterResult.failed("source_unreadable", "Page image payload does not match its manifest.", blocked=True)
        content, failure = self._fetch_detail(document_token)
        if failure:
            return failure
        inserted = False
        for item, payload in ordered:
            marker = self._media_marker(source_id, item)
            if marker in content:
                continue
            argv = (
                "lark-cli", "--as", "user", "docs", "+media-insert", "--doc", document_token,
                "--file", "{file}", "--caption", marker, "--align", "center", "--format", "json",
            )
            _data, failure = self._response(self.runner.upload(argv, payload=payload, name=item.file_name), "write_failed")
            if failure:
                return failure
            inserted = True
        if inserted:
            content, failure = self._fetch_detail(document_token)
            if failure:
                return failure
        expected = tuple(self._media_marker(source_id, item) for item, _payload in ordered)
        if any(marker not in content for marker in expected):
            return AdapterResult.failed("readback_failed", "Inserted page image marker is missing from readback.", blocked=True)
        checks = ("image_marker_readback", "page_number", "payload_sha256", "duplicate_guard")
        return AdapterResult.ok(checked=checks) if inserted else AdapterResult.reused(checked=checks)

    def _fetch_detail(self, document_token: str) -> tuple[str, AdapterResult | None]:
        argv = (
            "lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", document_token,
            "--doc-format", "markdown", "--detail", "with-ids", "--format", "json",
        )
        data, failure = self._response(self.runner.run(argv), "readback_failed")
        document = data.get("document") if isinstance(data.get("document"), dict) else None
        content = document.get("content") if document else None
        if failure or not isinstance(content, str):
            return "", failure or AdapterResult.failed("readback_failed", "Document detail is unreadable.", blocked=True)
        return content, None

    @staticmethod
    def _media_marker(source_id: str, media: MediaArtifact) -> str:
        return f"ZSK:{source_id}:P{media.page_number:03d}:{media.sha256[:16]}"

    def _store_doc(self, key: str, title: str, parent: str, payload: bytes, kind: str, locator: str, *, wrapped: bool = True) -> AdapterResult:
        replay = self._replay(key, payload)
        if replay:
            document = self._documents.get(key)
            metadata = {"document_url": document[1]} if document else {}
            factory = AdapterResult.reused if replay.status == "reused" else AdapterResult.ok
            return factory(*replay.object_refs, checked=replay.checked, metadata=metadata)
        document = self._document(title, payload) if wrapped else payload.decode("utf-8")
        node, failure = self._find(parent, title, "docx")
        if failure:
            return failure
        if node:
            token = node.get("obj_token")
            fetch = ("lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", token, "--doc-format", "markdown", "--format", "json")
            fetched, failure = self._response(self.runner.run(fetch), "readback_failed")
            fetched_document = fetched.get("document") if isinstance(fetched.get("document"), dict) else None
            if failure or not fetched_document or not self._matches_document(title, payload, str(fetched_document.get("content", "")), wrapped):
                return failure or AdapterResult.failed("version_conflict", "Existing Doc content differs.", blocked=True)
            ref = self._ref(key, kind, payload, locator)
            self._stored[key] = (hashlib.sha256(payload).hexdigest(), ref)
            url = self._document_url(node, token)
            self._documents[key] = (token, url)
            self._document_keys[ref.object_id] = key
            self._document_expectations[key] = (title, payload, wrapped, ())
            return AdapterResult.reused(ref, checked=("remote_doc_present", "content_readback"), metadata={"document_url": url})
        create = (
            "lark-cli", "--as", "user", "wiki", "nodes", "create", "--params",
            json.dumps({"space_id": self.space_id}, separators=(",", ":")), "--data",
            json.dumps({"obj_type": "docx", "node_type": "origin", "title": title, "parent_node_token": parent}, ensure_ascii=False, separators=(",", ":")), "--format", "json",
        )
        data, failure = self._response(self.runner.run(create), "write_failed")
        if failure:
            return failure
        node = data.get("node") if isinstance(data.get("node"), dict) else None
        token = node.get("obj_token") if node else None
        if not isinstance(token, str) or not token:
            return AdapterResult.failed("write_failed", "Created Doc has no stable token.")
        update = ("lark-cli", "--as", "user", "docs", "+update", "--api-version", "v2", "--doc", token, "--command", "overwrite", "--doc-format", "markdown", "--content", "-", "--format", "json")
        _written, failure = self._response(self.runner.run(update, stdin=document), "write_failed")
        if failure:
            return failure
        fetch = ("lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", token, "--doc-format", "markdown", "--format", "json")
        fetched, failure = self._response(self.runner.run(fetch), "readback_failed")
        if failure:
            return failure
        fetched_document = fetched.get("document") if isinstance(fetched.get("document"), dict) else None
        if not fetched_document or not self._matches_document(title, payload, str(fetched_document.get("content", "")), wrapped):
            return AdapterResult.failed("readback_failed", "Created Doc content failed readback.", blocked=True)
        ref = self._ref(key, kind, payload, locator)
        self._stored[key] = (hashlib.sha256(payload).hexdigest(), ref)
        url = self._document_url(node or {}, token)
        self._documents[key] = (token, url)
        self._document_keys[ref.object_id] = key
        self._document_expectations[key] = (title, payload, wrapped, ())
        return AdapterResult.ok(ref, checked=("wiki_doc_created", "markdown_written", "content_readback"), metadata={"document_url": url})

    @staticmethod
    def _document_url(node: Mapping[str, Any], token: str) -> str:
        candidate = node.get("url")
        return candidate if isinstance(candidate, str) and candidate.startswith("https://") else f"https://feishu.cn/docx/{token}"

    @staticmethod
    def _document(title: str, payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        body = payload.decode("utf-8").rstrip()
        return f"# {title}\n\n内容校验：`{digest}`\n\n{body}\n\n校验结束：`{digest}`\n"

    @staticmethod
    def _matches_document(title: str, payload: bytes, content: str, wrapped: bool = True) -> bool:
        digest = hashlib.sha256(payload).hexdigest()
        title_prefixes = (f"# {title}\n", f"<title>{title}</title>\n")
        if not content.rstrip().startswith(title_prefixes):
            return False
        return content.count(f"`{digest}`") == 2 if wrapped else payload.decode("utf-8").rstrip() in content

    def _replay(self, key: str, payload: bytes) -> AdapterResult | None:
        existing = self._stored.get(key)
        if existing is None:
            return None
        if existing[0] != hashlib.sha256(payload).hexdigest():
            return AdapterResult.failed("version_conflict", "Create-only Feishu object content differs.", blocked=True)
        return AdapterResult.reused(existing[1], checked=("create_only", "payload_sha256", "readback"))

    def _find(self, parent: str, title: str, kind: str) -> tuple[dict[str, Any] | None, AdapterResult | None]:
        argv = ("lark-cli", "--as", "user", "wiki", "nodes", "list", "--space-id", self.space_id, "--parent-node-token", parent, "--page-all", "--format", "json")
        data, failure = self._response(self.runner.run(argv), "readback_failed")
        if failure:
            return None, failure
        items = data.get("items")
        if items is None:
            items = []
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            return None, AdapterResult.failed("readback_failed", "Wiki child list is malformed.", blocked=True)
        matches = [item for item in items if item.get("title") == title]
        if len(matches) > 1 or matches and matches[0].get("obj_type") != kind:
            return None, AdapterResult.failed("structure_conflict", "Stage 5 object title is duplicated or has the wrong type.", blocked=True)
        if matches and not isinstance(matches[0].get("obj_token"), str):
            return None, AdapterResult.failed("readback_failed", "Stage 5 object has no stable token.", blocked=True)
        return (matches[0] if matches else None), None

    @staticmethod
    def _ref(key: str, kind: str, payload: bytes, locator: str) -> BackendObjectRef:
        digest = hashlib.sha256(payload).hexdigest()
        opaque = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return BackendObjectRef(f"feishu-{kind}-{opaque}", kind, f"{locator}/{opaque}", digest[:16])

    @classmethod
    def _response(cls, response: CliResponse, fallback: str) -> tuple[dict[str, Any], AdapterResult | None]:
        payload = cls._json(response.stdout) or cls._json(response.stderr)
        if response.returncode != 0 or not isinstance(payload, dict) or payload.get("ok") is False:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            marker = str(error.get("type") or error.get("code") or "").lower()
            code = "permission_denied" if marker in {"permission_denied", "forbidden", "insufficient_scope"} else "feishu_auth_missing" if marker in {"unauthorized", "authentication_failed", "authorization_failed"} else fallback
            return {}, AdapterResult.failed(code, "Feishu stage 5 operation failed.", blocked=code in {"permission_denied", "feishu_auth_missing", "readback_failed"})
        data = payload.get("data", payload)
        return (data, None) if isinstance(data, dict) else ({}, AdapterResult.failed(fallback, "Feishu response has no object data.", blocked=True))

    @staticmethod
    def _json(raw: str) -> dict[str, Any] | None:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            value = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
