"""飞书阶段 5 的薄 File/Doc 存储器；对象 token 不向上层暴露。"""
from __future__ import annotations
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Mapping
from .contracts import AdapterResult, BackendObjectRef, ExceptionRecord, PageArtifact, SourceRecord
from .feishu_cli import CliResponse, CliRunner
from .naming import safe_title, source_original_name
class FeishuStage5Storage:
    def __init__(self, runner: CliRunner, space_id: str, root_nodes: Mapping[str, str]) -> None:
        self.runner = runner
        self.space_id = space_id
        self.root_nodes = dict(root_nodes)
        self._stored: dict[str, tuple[str, BackendObjectRef]] = {}
        self._doc_tokens: dict[str, str] = {}
    def store_original(self, source: SourceRecord, payload: bytes) -> AdapterResult:
        if hashlib.sha256(payload).hexdigest() != source.original_sha256:
            return AdapterResult.failed("source_unreadable", "Original payload hash does not match its record.", blocked=True)
        key = f"{source.source_id}:original"
        replay = self._replay(key, payload)
        if replay:
            return replay
        registered, registered_failure = self._find_source_document(source.source_id)
        if registered_failure:
            return registered_failure
        if registered is not None:
            _readable_node, content = registered
            recorded_hash = self._frontmatter_value(content, "original_sha256")
            if recorded_hash != source.original_sha256:
                return AdapterResult.failed("version_conflict", "Registered source hash differs from this original.", blocked=True)
            display_name = self._frontmatter_value(content, "display_name")
            original_name = self._frontmatter_value(content, "original_file_name")
            if not isinstance(display_name, str) or not isinstance(original_name, str):
                return AdapterResult.failed("readback_failed", "Registered source naming metadata is incomplete.", blocked=True)
            original, original_failure = self._find(
                self.root_nodes["01"], source_original_name(display_name, original_name), "file"
            )
            if original_failure or not original:
                return original_failure or AdapterResult.failed("ownership_unknown", "Registered source original is missing.", blocked=True)
            ref = self._ref(key, "source_original", payload, "feishu://01/original")
            self._stored[key] = (source.original_sha256, ref)
            return AdapterResult.reused(ref, checked=("source_identity_verified", "payload_sha256", "human_readable_name"))
        name = source_original_name(source.display_name or source.source_title, source.original_name)
        node, failure = self._find(self.root_nodes["01"], name, "file")
        if failure:
            return failure
        if node:
            return AdapterResult.failed("version_conflict", "A different source already uses this human-readable file name.", blocked=True)
        argv = (
            "lark-cli", "--as", "user", "drive", "+upload", "--file", "{file}",
            "--wiki-token", self.root_nodes["01"], "--name", name, "--format", "json",
        )
        data, failure = self._response(self.runner.upload(argv, payload=payload, name=name), "write_failed")
        if failure:
            return failure
        token = data.get("file_token") or data.get("token")
        if not isinstance(token, str) or not token:
            return AdapterResult.failed("readback_failed", "Uploaded File has no stable token.", blocked=True)
        ref = self._ref(key, "source_original", payload, "feishu://01/original")
        self._stored[key] = (hashlib.sha256(payload).hexdigest(), ref)
        return AdapterResult.ok(ref, checked=("drive_file_uploaded", "human_readable_name", "stable_file_token"))
    def store_readable(self, source: SourceRecord, payload: bytes) -> AdapterResult:
        if hashlib.sha256(payload).hexdigest() != source.readable_sha256:
            return AdapterResult.failed("source_unreadable", "Readable payload hash does not match its record.", blocked=True)
        title = safe_title(source.display_name or source.source_title)
        return self._store_doc(f"{source.source_id}:readable", title, self.root_nodes["01"], payload, "source_readable", "feishu://01/readable")
    def store_page_evidence(self, source: SourceRecord, page: PageArtifact, payload: bytes) -> AdapterResult:
        if page.source_id != source.source_id or page not in source.page_artifacts:
            return AdapterResult.failed("binding_conflict", "Page evidence does not belong to the source.", blocked=True)
        if hashlib.sha256(payload).hexdigest() != page.sha256:
            return AdapterResult.failed("source_unreadable", "Page payload hash does not match its manifest.", blocked=True)
        if page.width_px < 1 or page.height_px < 1:
            return AdapterResult.failed("source_unreadable", "Page dimensions are required for Feishu media verification.", blocked=True)
        key = f"{source.source_id}:page:{page.page_number:03d}"
        token = self._doc_tokens.get(f"{source.source_id}:readable")
        if token is None:
            registered, failure = self._find_source_document(source.source_id)
            if failure:
                return failure
            if registered is None:
                return AdapterResult.failed("ownership_unknown", "Readable source document is missing before page media insert.", blocked=True)
            readable_node, _content = registered
            token = readable_node.get("obj_token")
        if not isinstance(token, str) or not token:
            return AdapterResult.failed("readback_failed", "Readable source document has no stable token.", blocked=True)
        before, failure = self._fetch_full_document(token)
        if failure:
            return failure
        caption = f"第 {page.page_number} 页｜SHA256 {page.sha256}"
        existing = self._matching_media(before, caption)
        inserted_token: str | None = None
        created = False
        if not existing:
            argv = (
                "lark-cli", "--as", "user", "docs", "+media-insert", "--doc", token,
                "--file", "{file}", "--width", str(page.width_px), "--height", str(page.height_px),
                "--align", "center", "--caption", caption, "--format", "json",
            )
            name = page.file_name
            data, failure = self._response(self.runner.upload(argv, payload=payload, name=name), "write_failed")
            if failure:
                return failure
            inserted_token = data.get("file_token") or data.get("token")
            if not isinstance(inserted_token, str) or not inserted_token:
                return AdapterResult.failed("readback_failed", "Inserted page media has no stable token.", blocked=True)
            created = True
        after, failure = self._fetch_full_document(token)
        if failure:
            return failure
        matches = self._matching_media(after, caption, inserted_token)
        source_images = self._source_evidence_media(after)
        if len(matches) != 1 or len(source_images) != page.page_number:
            return AdapterResult.failed("readback_failed", "Source page image count or identity failed readback.", blocked=True)
        media = matches[0]
        if media["width_px"] != page.width_px or media["height_px"] != page.height_px:
            return AdapterResult.failed("readback_failed", "Source page image dimensions failed readback.", blocked=True)
        media_token = media["token"]
        download = (
            "lark-cli", "--as", "user", "docs", "+media-download", "--token", media_token,
            "--output", "{output}", "--format", "json",
        )
        response, remote_payload = self.runner.download(download, name=page.file_name)
        _downloaded, failure = self._response(response, "readback_failed")
        if failure:
            return failure
        if hashlib.sha256(remote_payload).hexdigest() != page.sha256:
            return AdapterResult.failed("readback_failed", "Downloaded page media hash differs from the approved page.", blocked=True)
        ref = self._ref(key, "source_page", payload, f"feishu://01/pages/{page.page_number:03d}")
        self._stored[key] = (page.sha256, ref)
        factory = AdapterResult.ok if created else AdapterResult.reused
        return factory(
            ref,
            checked=("source_doc_media_inserted", "document_full_readback", "image_count_readback", "image_dimensions_readback", "media_sha256_readback"),
            metadata={"image_count": len(source_images), "width_px": page.width_px, "height_px": page.height_px, "sha256": page.sha256},
        )
    def write_exception(self, exception: ExceptionRecord) -> AdapterResult:
        payload = (f"原因码：`{exception.reason_code}`\n\n{exception.safe_note}\n\n待确认：{exception.question}\n").encode("utf-8")
        return self._store_doc(f"exception:{exception.exception_id}", exception.exception_id, self.root_nodes["02"], payload, "exception", "feishu://02")

    def store_document(self, key: str, title: str, parent: str, payload: bytes, kind: str, locator: str) -> AdapterResult:
        return self._store_doc(key, title, parent, payload, kind, locator, wrapped=False)

    def registered_source(self, source_id: str, source_role: str) -> AdapterResult:
        if all(key in self._stored for key in (f"{source_id}:original", f"{source_id}:readable")):
            return AdapterResult.ok(checked=("source_original_present", "source_readable_present", "source_identity_verified"))
        found, failure = self._find_source_document(source_id)
        if failure:
            return failure
        if found is not None:
            _node, content = found
            display_name = self._frontmatter_value(content, "display_name")
            original_name = self._frontmatter_value(content, "original_file_name")
            if not isinstance(display_name, str) or not isinstance(original_name, str):
                return AdapterResult.failed("readback_failed", "Registered source naming metadata is incomplete.", blocked=True)
            original, original_failure = self._find(
                self.root_nodes["01"], source_original_name(display_name, original_name), "file"
            )
            if original_failure or not original:
                return original_failure or AdapterResult.failed("ownership_unknown", "Registered source original is missing.", blocked=True)
            return AdapterResult.ok(checked=("source_original_present", "source_readable_present", "source_identity_verified", "human_readable_names"))
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
        return AdapterResult.ok(checked=("source_original_present", "source_readable_present", "source_identity_verified"))

    def _find_source_document(self, source_id: str) -> tuple[tuple[dict[str, Any], str] | None, AdapterResult | None]:
        argv = ("lark-cli", "--as", "user", "wiki", "nodes", "list", "--space-id", self.space_id, "--parent-node-token", self.root_nodes["01"], "--page-all", "--format", "json")
        data, failure = self._response(self.runner.run(argv), "readback_failed")
        if failure:
            return None, failure
        items = data.get("items") or []
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            return None, AdapterResult.failed("readback_failed", "Wiki child list is malformed.", blocked=True)
        matches: list[tuple[dict[str, Any], str]] = []
        marker = f'source_id: "{source_id}"'
        for item in items:
            if item.get("obj_type") != "docx" or not isinstance(item.get("obj_token"), str):
                continue
            fetch = ("lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", item["obj_token"], "--doc-format", "markdown", "--format", "json")
            fetched, fetch_failure = self._response(self.runner.run(fetch), "readback_failed")
            if fetch_failure:
                return None, fetch_failure
            document = fetched.get("document") if isinstance(fetched.get("document"), dict) else None
            content = str(document.get("content", "")) if document else ""
            if marker in content:
                matches.append((item, content))
        if len(matches) > 1:
            return None, AdapterResult.failed("duplicate_conflict", "More than one readable source has the same source identity.", blocked=True)
        if matches:
            token = matches[0][0].get("obj_token")
            if isinstance(token, str) and token:
                self._doc_tokens[f"{source_id}:readable"] = token
        return (matches[0] if matches else None), None

    def _fetch_full_document(self, token: str) -> tuple[dict[str, Any], AdapterResult | None]:
        argv = (
            "lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", token,
            "--doc-format", "xml", "--detail", "full", "--format", "json",
        )
        data, failure = self._response(self.runner.run(argv), "readback_failed")
        if failure:
            return {}, failure
        document = data.get("document") if isinstance(data.get("document"), dict) else None
        if document is None or not isinstance(document.get("content"), str):
            return {}, AdapterResult.failed("readback_failed", "Full source document content is unavailable.", blocked=True)
        return document, None

    @staticmethod
    def _document_media(document: Mapping[str, Any]) -> list[dict[str, Any]]:
        content = str(document.get("content", ""))
        try:
            root = ET.fromstring(f"<zsk-root>{content}</zsk-root>")
        except ET.ParseError:
            return []
        reference_map = document.get("reference_map") if isinstance(document.get("reference_map"), dict) else {}
        media: list[dict[str, Any]] = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "img":
                continue
            attributes: dict[str, Any] = dict(element.attrib)
            ref = attributes.get("ref")
            if isinstance(ref, str):
                for group in reference_map.values():
                    if isinstance(group, dict) and isinstance(group.get(ref), dict):
                        attributes.update(group[ref])
            token = attributes.get("token") or attributes.get("file_token") or attributes.get("src")
            try:
                width = int(attributes.get("width") or attributes.get("width_px") or 0)
                height = int(attributes.get("height") or attributes.get("height_px") or 0)
            except (TypeError, ValueError):
                width = height = 0
            if isinstance(token, str) and token:
                media.append({
                    "token": token,
                    "width_px": width,
                    "height_px": height,
                    "caption": str(attributes.get("caption") or "").strip(),
                })
        return media

    @classmethod
    def _source_evidence_media(cls, document: Mapping[str, Any]) -> list[dict[str, Any]]:
        marker = re.compile(r"^第 \d+ 页｜SHA256 [0-9a-f]{64}$")
        return [item for item in cls._document_media(document) if marker.fullmatch(item["caption"])]

    @classmethod
    def _matching_media(cls, document: Mapping[str, Any], caption: str, token: str | None = None) -> list[dict[str, Any]]:
        return [
            item for item in cls._document_media(document)
            if item["caption"] == caption and (token is None or item["token"] == token)
        ]

    @staticmethod
    def _frontmatter_value(content: str, key: str) -> object:
        for line in content.splitlines():
            if line.startswith(f"{key}:"):
                try:
                    return json.loads(line.partition(":")[2].strip())
                except json.JSONDecodeError:
                    return None
        return None

    def registered_business_source(self, source_id: str) -> AdapterResult:
        return self.registered_source(source_id, "business_knowledge")

    def _store_doc(self, key: str, title: str, parent: str, payload: bytes, kind: str, locator: str, *, wrapped: bool = True) -> AdapterResult:
        replay = self._replay(key, payload)
        if replay:
            return replay
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
            effective_locator = f"feishu://doc/{token}" if kind in {"knowledge_asset", "method_asset", "profile"} else locator
            ref = self._ref(key, kind, payload, effective_locator)
            self._stored[key] = (hashlib.sha256(payload).hexdigest(), ref)
            if isinstance(token, str) and token:
                self._doc_tokens[key] = token
            return AdapterResult.reused(ref, checked=("remote_doc_present", "content_readback"))
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
        effective_locator = f"feishu://doc/{token}" if kind in {"knowledge_asset", "method_asset", "profile"} else locator
        ref = self._ref(key, kind, payload, effective_locator)
        self._stored[key] = (hashlib.sha256(payload).hexdigest(), ref)
        self._doc_tokens[key] = token
        return AdapterResult.ok(ref, checked=("wiki_doc_created", "markdown_written", "content_readback"))

    @staticmethod
    def _document(title: str, payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        body = payload.decode("utf-8").rstrip()
        return f"# {title}\n\n内容校验：`{digest}`\n\n{body}\n\n校验结束：`{digest}`\n"

    @staticmethod
    def _matches_document(title: str, payload: bytes, content: str, wrapped: bool = True) -> bool:
        digest = hashlib.sha256(payload).hexdigest()
        title_prefixes = (f"# {title}\n", f"<title>{title}</title>\n")
        normalized = content.rstrip()
        is_markdown = normalized.startswith(title_prefixes)
        xml_root: ET.Element | None = None
        if not is_markdown and normalized.startswith("<title"):
            try:
                xml_root = ET.fromstring(f"<zsk-root>{normalized}</zsk-root>")
            except ET.ParseError:
                return False
            title_node = next((item for item in xml_root if item.tag.rsplit("}", 1)[-1] == "title"), None)
            if title_node is None or "".join(title_node.itertext()) != title:
                return False
        elif not is_markdown:
            return False
        expected_body = payload.decode("utf-8").rstrip()
        expected_tokens = re.findall(r"[0-9A-Za-z_\u4e00-\u9fff]+", expected_body)
        remote_tokens = re.findall(r"[0-9A-Za-z_\u4e00-\u9fff]+", content if is_markdown else " ".join(xml_root.itertext()) if xml_root is not None else "")

        def contains_ordered_body() -> bool:
            width = len(expected_tokens)
            return width == 0 or any(remote_tokens[index:index + width] == expected_tokens for index in range(len(remote_tokens) - width + 1))

        if not wrapped:
            return contains_ordered_body()
        remote_text = content if is_markdown else " ".join(xml_root.itertext()) if xml_root is not None else ""
        marker_count = content.count(f"`{digest}`") if is_markdown else remote_text.count(digest)
        return marker_count == 2 and contains_ordered_body()

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
