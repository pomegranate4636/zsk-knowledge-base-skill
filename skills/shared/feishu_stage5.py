"""飞书阶段 5 的薄 File/Doc 存储器；对象 token 不向上层暴露。"""
from __future__ import annotations
import hashlib
import json
from typing import Any, Mapping, Sequence
from .contracts import AdapterResult, BackendObjectRef, ExceptionRecord, PageArtifact, SourceRecord
from .feishu_cli import CliResponse, CliRunner
class FeishuStage5Storage:
    def __init__(self, runner: CliRunner, space_id: str, root_nodes: Mapping[str, str]) -> None:
        self.runner = runner
        self.space_id = space_id
        self.root_nodes = dict(root_nodes)
        self._stored: dict[str, tuple[str, BackendObjectRef]] = {}
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
    def store_page_evidence(self, source: SourceRecord, page: PageArtifact, payload: bytes) -> AdapterResult:
        if page.source_id != source.source_id or page not in source.page_artifacts:
            return AdapterResult.failed("binding_conflict", "Page evidence does not belong to the source.", blocked=True)
        if hashlib.sha256(payload).hexdigest() != page.sha256:
            return AdapterResult.failed("source_unreadable", "Page payload hash does not match its manifest.", blocked=True)
        key = f"{source.source_id}:page:{page.page_number:03d}"
        replay = self._replay(key, payload)
        if replay:
            return replay
        name = f"{source.source_id}-page-{page.page_number:03d}-{page.sha256[:16]}.png"
        node, failure = self._find(self.root_nodes["01"], name, "file")
        if failure:
            return failure
        if node:
            ref = self._ref(key, "source_page", payload, f"feishu://01/pages/{page.page_number:03d}")
            self._stored[key] = (page.sha256, ref)
            return AdapterResult.reused(ref, checked=("remote_page_present", "content_addressed_name", "stable_file_token"))
        argv = (
            "lark-cli", "--as", "user", "drive", "+upload", "--file", "{file}",
            "--wiki-token", self.root_nodes["01"], "--name", name, "--format", "json",
        )
        data, failure = self._response(self.runner.upload(argv, payload=payload, name=name), "write_failed")
        if failure:
            return failure
        token = data.get("file_token") or data.get("token")
        if not isinstance(token, str) or not token:
            return AdapterResult.failed("readback_failed", "Uploaded page has no stable token.", blocked=True)
        listed, list_failure = self._find(self.root_nodes["01"], name, "file")
        if list_failure or not listed:
            return list_failure or AdapterResult.failed("readback_failed", "Uploaded page cannot be read back by its content-addressed name.", blocked=True)
        ref = self._ref(key, "source_page", payload, f"feishu://01/pages/{page.page_number:03d}")
        self._stored[key] = (page.sha256, ref)
        return AdapterResult.ok(ref, checked=("drive_page_uploaded", "content_addressed_name", "stable_file_token", "list_readback"))
    def write_exception(self, exception: ExceptionRecord) -> AdapterResult:
        payload = (f"原因码：`{exception.reason_code}`\n\n{exception.safe_note}\n\n待确认：{exception.question}\n").encode("utf-8")
        return self._store_doc(f"exception:{exception.exception_id}", exception.exception_id, self.root_nodes["02"], payload, "exception", "feishu://02")

    def store_document(self, key: str, title: str, parent: str, payload: bytes, kind: str, locator: str) -> AdapterResult:
        return self._store_doc(key, title, parent, payload, kind, locator, wrapped=False)

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
            ref = self._ref(key, kind, payload, locator)
            self._stored[key] = (hashlib.sha256(payload).hexdigest(), ref)
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
        ref = self._ref(key, kind, payload, locator)
        self._stored[key] = (hashlib.sha256(payload).hexdigest(), ref)
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
        if not content.rstrip().startswith(title_prefixes):
            return False
        return content.count(f"`{digest}`") == 2 if wrapped else content.rstrip().endswith(payload.decode("utf-8").rstrip())

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
