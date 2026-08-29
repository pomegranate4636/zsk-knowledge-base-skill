"""飞书知识库 Adapter；私有 token 不进入 Registry、Result、Evidence 或日志。"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Sequence
from urllib.parse import urlsplit
from .contracts import AdapterResult, AssetPayload, BackendObjectRef, Binding, ExceptionRecord, PageArtifact, SourceRecord, ROOT_KEYS
from .feishu_cli import CliRunner, SubprocessCliRunner
from .feishu_stage5 import FeishuStage5Storage
from .templates import ROOT_TITLES, root_content
_WIKI_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")
_SPACE_ID = re.compile(r"^[0-9]+$")
_MIN_CLI_VERSION = (1, 0, 89)
_REQUIRED_SCOPES = frozenset({"wiki:node:create", "wiki:node:read", "wiki:node:retrieve", "wiki:space:read", "docx:document:create", "docx:document:readonly", "docx:document:write_only"})
@dataclass(frozen=True)
class _Target:
    space_id: str
    parent_node_token: str
    visibility: str
    open_sharing: str
@dataclass(frozen=True)
class _Root:
    key: str
    node_token: str
    object_token: str
    version: str
class FeishuAdapter:
    """实现既有 12 方法合同，阶段 3 之外的写入明确停止。"""
    def __init__(self, runner: CliRunner | None = None) -> None:
        self._runner = runner or SubprocessCliRunner()
        self._binding_key: str | None = None
        self._target: _Target | None = None
        self._roots: dict[str, _Root] = {}
        self._stage5: FeishuStage5Storage | None = None
        self._source_refs: dict[str, BackendObjectRef] = {}
        self._asset_refs: dict[str, BackendObjectRef] = {}
    def doctor(self) -> AdapterResult:
        version = self._runner.run(("lark-cli", "--version"))
        parsed = re.fullmatch(r"lark-cli version (\d+)\.(\d+)\.(\d+)", version.stdout.strip())
        if version.returncode != 0 or not parsed:
            return AdapterResult.failed("feishu_cli_missing", "Feishu CLI is unavailable.", blocked=True)
        if tuple(int(part) for part in parsed.groups()) < _MIN_CLI_VERSION:
            return AdapterResult.failed("dependency_missing", "Feishu CLI is below the stage 3 minimum version.", blocked=True)
        status, failure = self._json(("lark-cli", "auth", "status", "--verify", "--json"), "feishu_auth_missing")
        if failure:
            return failure
        user = status.get("identities", {}).get("user", {}) if isinstance(status, dict) else {}
        scopes = set(str(user.get("scope", "")).split()) if isinstance(user, dict) else set()
        if user.get("status") != "ready" or user.get("tokenStatus") != "valid" or user.get("verified") is not True:
            return AdapterResult.failed("feishu_auth_missing", "User identity or token is not ready.", blocked=True)
        if not _REQUIRED_SCOPES.issubset(scopes):
            return AdapterResult.failed("permission_denied", "Required Feishu scopes are missing.", blocked=True)
        return AdapterResult.ok(checked=("feishu_cli_version", "user_identity_ready", "user_token_valid", "required_scopes"))

    def resolve_binding(self, binding: Binding) -> AdapterResult:
        if binding.backend_type != "feishu":
            return AdapterResult.failed("backend_unsupported", "Feishu Adapter only accepts the feishu backend.", blocked=True)
        key = self._binding_digest(binding)
        if self._binding_key is not None and self._binding_key != key:
            return AdapterResult.failed("binding_conflict", "Adapter is already resolved for another binding.", blocked=True)
        if self._target is not None:
            return AdapterResult.reused(checked=("client_id", "wiki_target", "space_access"), metadata=self._space_metadata())
        target = self._wiki_target(binding.backend_locator)
        if target is None:
            return AdapterResult.failed("binding_missing", "backend_locator must be a Feishu Wiki space or node URL.", blocked=True)
        space_id, parent = target
        if parent:
            node, failure = self._json(self._wiki_get_node(parent), "binding_missing")
            if failure:
                return failure
            node = node.get("node") if isinstance(node, dict) else None
            remote_space = node.get("space_id") if isinstance(node, dict) else None
            if not isinstance(remote_space, str) or not remote_space:
                return AdapterResult.failed("readback_failed", "Wiki target response has no stable node identity.", blocked=True)
            space_id = remote_space
        space, failure = self._json(self._wiki_get_space(space_id), "binding_missing")
        if failure:
            return failure
        space = space.get("space") if isinstance(space, dict) else None
        if not isinstance(space, dict):
            return AdapterResult.failed("readback_failed", "Wiki space response is incomplete.", blocked=True)
        visibility, sharing = space.get("visibility"), space.get("open_sharing")
        if not isinstance(visibility, str) or not isinstance(sharing, str):
            return AdapterResult.failed("readback_failed", "Wiki sharing metadata is incomplete.", blocked=True)
        self._binding_key = key
        self._target = _Target(space_id, parent, visibility, sharing)
        return AdapterResult.ok(checked=("client_id", "wiki_target", "space_access"), metadata=self._space_metadata())

    def inspect_structure(self, binding: Binding) -> AdapterResult:
        guard = self._binding_guard(binding)
        if guard:
            return guard
        roots, missing, failure = self._inspect_remote_roots(binding)
        if failure:
            return failure
        self._roots = roots
        refs = tuple(self._ref(binding, root) for root in roots.values())
        if not roots:
            return AdapterResult.ok(checked=("nine_root_objects_absent",), metadata={"structure_state": "empty", "missing_root_keys": list(missing)})
        if missing:
            return AdapterResult.ok(*refs, checked=("partial_root_objects",), metadata={"structure_state": "partial", "missing_root_keys": list(missing)})
        return AdapterResult.reused(*refs, checked=("nine_root_objects_present", "docx_content_mapped"), metadata={"structure_state": "complete", "missing_root_keys": []})

    def create_skeleton(self, binding: Binding) -> AdapterResult:
        guard = self._binding_guard(binding)
        if guard:
            return guard
        roots, missing, failure = self._inspect_remote_roots(binding)
        if failure:
            return failure
        if not missing:
            self._roots = roots
            return AdapterResult.reused(*tuple(self._ref(binding, root) for root in roots.values()), checked=("create_only", "nine_root_objects_present"))
        for key in missing:
            created, failure = self._create_root(binding, key)
            if failure:
                return failure
            roots[key] = created
        self._roots = roots
        final = self.inspect_structure(binding)
        if final.status not in {"ok", "reused"}:
            return final
        check = "nine_root_objects_created" if len(missing) == len(ROOT_KEYS) else "missing_root_objects_created"
        return AdapterResult.ok(*final.object_refs, checked=("create_only", check, "docx_content_written", "docx_content_readback"))

    def read_rules(self, binding: Binding) -> AdapterResult:
        guard = self._binding_guard(binding)
        if guard:
            return guard
        inspected = self.inspect_structure(binding)
        if inspected.status != "reused":
            return AdapterResult.failed("structure_conflict", "Rules require a complete, unchanged skeleton.", blocked=True)
        return AdapterResult.ok(self._ref(binding, self._roots["AGENTS"]), self._ref(binding, self._roots["README"]), checked=("rules_read", "customer_owned_rules_preserved"))
    def store_original(self, binding: Binding, source: SourceRecord, payload: bytes) -> AdapterResult:
        storage, failure = self._stage5_source_storage(binding, source)
        return self._remember_source(failure or storage.store_original(source, payload))
    def store_readable(self, binding: Binding, source: SourceRecord, payload: bytes) -> AdapterResult:
        storage, failure = self._stage5_source_storage(binding, source)
        return self._remember_source(failure or storage.store_readable(source, payload))
    def store_page_evidence(self, binding: Binding, source: SourceRecord, page: PageArtifact, payload: bytes) -> AdapterResult:
        storage, failure = self._stage5_source_storage(binding, source)
        return self._remember_source(failure or storage.store_page_evidence(source, page, payload))
    def write_exception(self, binding: Binding, exception: ExceptionRecord) -> AdapterResult:
        storage, failure = self._stage5_storage(binding)
        return failure or storage.write_exception(exception)
    def write_knowledge_asset(self, binding: Binding, asset: AssetPayload) -> AdapterResult:
        if not isinstance(asset, AssetPayload):
            return self._later_stage("write_knowledge_asset")
        storage, failure = self._stage5_storage(binding)
        source = failure or storage.registered_source(asset.source_id, "business_knowledge")
        body = asset.body.partition("\n\n")[2].encode("utf-8")
        return source if source.status not in {"ok", "reused"} else self._remember_asset(storage.store_document(f"knowledge:{asset.asset_id}", asset.title, storage.root_nodes["03"], body, "knowledge_asset", "feishu://03"))
    def write_method_asset(self, binding: Binding, asset: AssetPayload) -> AdapterResult:
        if not isinstance(asset, AssetPayload):
            return self._later_stage("write_method_asset")
        storage, failure = self._stage5_storage(binding)
        source = failure or storage.registered_source(asset.source_id, "reference_method")
        body = asset.body.partition("\n\n")[2].encode("utf-8")
        return source if source.status not in {"ok", "reused"} else self._remember_asset(storage.store_document(f"method:{asset.asset_id}", asset.title, storage.root_nodes["04"], body, "method_asset", "feishu://04"))
    def write_profile(self, binding: Binding, asset: AssetPayload) -> AdapterResult:
        if not isinstance(asset, AssetPayload):
            return self._later_stage("write_profile")
        storage, failure = self._stage5_storage(binding)
        source = failure or storage.registered_source(asset.source_id, "profile_material")
        return source if source.status not in {"ok", "reused"} else self._remember_asset(storage.store_document(f"profile:{asset.asset_id}", asset.title, storage.root_nodes["05"], asset.body.encode("utf-8"), "profile", "feishu://05"))

    def read_back(self, binding: Binding, refs: Sequence[BackendObjectRef] | None = None) -> AdapterResult:
        guard = self._binding_guard(binding)
        if guard:
            return guard
        inspected = self.inspect_structure(binding)
        if inspected.status != "reused":
            return AdapterResult.failed("readback_failed", "Root structure cannot be read back unchanged.", blocked=True)
        current = {ref.object_id: ref for ref in inspected.object_refs} | self._source_refs | self._asset_refs
        wanted = tuple(refs or tuple(current.values()))
        if not wanted or any(current.get(ref.object_id) != ref for ref in wanted):
            return AdapterResult.failed("readback_failed", "Stable root references changed or are unknown.", blocked=True)
        if any(ref.object_id in self._source_refs for ref in wanted):
            checks = ("source_write_readback", "payload_sha256", "stable_refs")
        elif any(ref.object_id in self._asset_refs for ref in wanted):
            checks = ("asset_write_readback", "stable_refs")
        else:
            checks = ("objects_present", "binding_identity", "stable_refs", "docx_content_fingerprints")
        return AdapterResult.ok(*wanted, checked=checks)

    def _inspect_remote_roots(self, binding: Binding) -> tuple[dict[str, _Root], tuple[str, ...], AdapterResult | None]:
        nodes, failure = self._list_children()
        if failure:
            return {}, (), failure
        roots: dict[str, _Root] = {}
        for key in ROOT_KEYS:
            matches = [item for item in nodes if item.get("title") == ROOT_TITLES[key]]
            if len(matches) > 1:
                return {}, (), AdapterResult.failed("structure_conflict", "Duplicate root title found in the target Wiki node.", blocked=True)
            if not matches:
                continue
            root, failure = self._validate_root(binding, key, matches[0])
            if failure:
                return {}, (), failure
            roots[key] = root
        missing = tuple(key for key in ROOT_KEYS if key not in roots)
        return roots, missing, None

    def _create_root(self, binding: Binding, key: str) -> tuple[_Root | None, AdapterResult | None]:
        target = self._target
        assert target is not None
        data = {"obj_type": "docx", "node_type": "origin", "title": ROOT_TITLES[key]}
        if target.parent_node_token:
            data["parent_node_token"] = target.parent_node_token
        created, failure = self._json(self._wiki_create(target.space_id, data), "write_failed")
        if failure:
            return None, failure
        node = created.get("node") if isinstance(created, dict) else None
        if not isinstance(node, dict):
            return None, AdapterResult.failed("write_failed", "Wiki did not return a created node.")
        root, failure = self._validate_root(binding, key, node, check_content=False)
        if failure:
            return None, failure
        updated, failure = self._json(self._doc_overwrite(root.object_token), "write_failed", stdin=self._document_content(binding, key))
        if failure:
            return None, failure
        if isinstance(updated, dict) and updated.get("result") not in {None, "success"}:
            return None, AdapterResult.failed("write_failed", "Docx content write did not succeed.")
        verified, failure = self._json(self._wiki_get_node(root.node_token), "readback_failed")
        node = verified.get("node") if isinstance(verified, dict) else None
        if failure or not isinstance(node, dict):
            return None, failure or AdapterResult.failed("readback_failed", "Created Wiki root cannot be read back.", blocked=True)
        return self._validate_root(binding, key, node)

    def _validate_root(self, binding: Binding, key: str, node: dict[str, Any], *, check_content: bool = True) -> tuple[_Root | None, AdapterResult | None]:
        target = self._target
        assert target is not None
        if node.get("node_type") != "origin" or node.get("obj_type") != "docx" or node.get("title") != ROOT_TITLES[key]:
            return None, AdapterResult.failed("structure_conflict", "A matching root title has the wrong Wiki object type.", blocked=True)
        if node.get("space_id") != target.space_id or (target.parent_node_token and node.get("parent_node_token") != target.parent_node_token):
            return None, AdapterResult.failed("structure_conflict", "A matching root belongs to another Wiki location.", blocked=True)
        node_token, object_token = node.get("node_token"), node.get("obj_token")
        if not isinstance(node_token, str) or not node_token or not isinstance(object_token, str) or not object_token:
            return None, AdapterResult.failed("readback_failed", "Wiki root has no stable object reference.", blocked=True)
        if not check_content:
            return _Root(key, node_token, object_token, str(node.get("obj_edit_time") or "1")), None
        fetched, failure = self._json(self._doc_fetch(object_token), "readback_failed")
        document = fetched.get("document") if isinstance(fetched, dict) else None
        content = document.get("content") if isinstance(document, dict) else None
        if failure or not isinstance(content, str):
            return None, failure or AdapterResult.failed("readback_failed", "Docx content cannot be read back.", blocked=True)
        if not self._content_matches(binding, key, content):
            return None, AdapterResult.failed("structure_conflict", "Existing root content is customer-owned or differs from the template.", blocked=True)
        version = str(node.get("obj_edit_time") or document.get("revision_id") or "1")
        return _Root(key, node_token, object_token, version), None

    def _list_children(self) -> tuple[list[dict[str, Any]], AdapterResult | None]:
        target = self._target
        assert target is not None
        items: list[dict[str, Any]] = []
        page_token = ""
        seen: set[str] = set()
        while True:
            data, failure = self._json(self._wiki_list(target.space_id, target.parent_node_token, page_token), "readback_failed")
            if failure:
                return [], failure
            page = data.get("items") if isinstance(data, dict) else None
            has_more = data.get("has_more") if isinstance(data, dict) else None
            if page is None and has_more is False:
                page = []
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                return [], AdapterResult.failed("readback_failed", "Wiki child list is malformed.", blocked=True)
            items.extend(page)
            if not has_more:
                return items, None
            page_token = data.get("page_token") if isinstance(data, dict) else ""
            if not isinstance(page_token, str) or not page_token or page_token in seen:
                return [], AdapterResult.failed("readback_failed", "Wiki pagination cannot be completed safely.", blocked=True)
            seen.add(page_token)

    def _binding_guard(self, binding: Binding) -> AdapterResult | None:
        if self._target is None or self._binding_key is None:
            return AdapterResult.failed("binding_missing", "Binding has not been resolved.", blocked=True)
        if self._binding_key != self._binding_digest(binding):
            return AdapterResult.failed("binding_conflict", "Resolved binding differs from requested binding.", blocked=True)
        return None

    def _stage5_storage(self, binding: Binding) -> tuple[FeishuStage5Storage | None, AdapterResult | None]:
        guard = self._binding_guard(binding)
        if guard:
            return None, guard
        inspected = self.inspect_structure(binding)
        if inspected.status != "reused":
            failure = inspected if inspected.status in {"blocked", "failed"} else AdapterResult.failed("structure_conflict", "Stage 5 requires a complete skeleton.", blocked=True)
            return None, failure
        if self._stage5 is None:
            assert self._target is not None
            self._stage5 = FeishuStage5Storage(self._runner, self._target.space_id, {key: self._roots[key].node_token for key in ("01", "02", "03", "04", "05")})
        return self._stage5, None

    def _remember_asset(self, result: AdapterResult) -> AdapterResult:
        if result.status in {"ok", "reused"}:
            for ref in result.object_refs:
                self._asset_refs[ref.object_id] = ref
        return result

    def _remember_source(self, result: AdapterResult) -> AdapterResult:
        if result.status in {"ok", "reused"}:
            for ref in result.object_refs:
                self._source_refs[ref.object_id] = ref
        return result

    def _stage5_source_storage(self, binding: Binding, source: SourceRecord) -> tuple[FeishuStage5Storage | None, AdapterResult | None]:
        storage, failure = self._stage5_storage(binding)
        if failure:
            return None, failure
        if not isinstance(source, SourceRecord) or source.client_id != binding.client_id:
            return None, AdapterResult.failed("binding_conflict", "Source belongs to another binding.", blocked=True)
        return storage, None

    @staticmethod
    def _binding_digest(binding: Binding) -> str:
        stable = {
            "schema_version": binding.schema_version, "client_id": binding.client_id,
            "subject_type": binding.subject_type, "backend_type": binding.backend_type,
            "backend_locator": FeishuAdapter._wiki_target(binding.backend_locator) or binding.backend_locator, "root_map": dict(binding.root_map),
            "template_version": binding.template_version, "status": binding.status,
        }
        return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _document_content(binding: Binding, key: str) -> str:
        source = root_content(binding, key)
        _heading, _separator, body = source.partition("\n")
        heading = f"# {ROOT_TITLES[key]}"
        if key in {"AGENTS", "README"}:
            return f"{heading}\n\n客户名称：{binding.client_name}\n知识库名称：{binding.knowledge_base_name}\n{body}"
        return f"{heading}\n{body}" if source else f"{heading}\n"

    def _content_matches(self, binding: Binding, key: str, content: str) -> bool:
        # 根节点的标题、类型和位置由 _validate_root 固定；说明正文属于客户，可编辑但不能为空。
        return bool(content.strip())

    def _ref(self, binding: Binding, root: _Root) -> BackendObjectRef:
        opaque = hashlib.sha256(f"{binding.client_id}:{root.key}".encode("utf-8")).hexdigest()[:16]
        return BackendObjectRef(f"feishu-root-{opaque}", "wiki_docx_root", f"feishu://root/{root.key}", root.version)

    def _space_metadata(self) -> dict[str, str]:
        target = self._target
        assert target is not None
        return {"visibility": target.visibility, "open_sharing": target.open_sharing}

    @staticmethod
    def _wiki_target(locator: str) -> tuple[str, str] | None:
        parsed = urlsplit(locator)
        parts = tuple(part for part in parsed.path.split("/") if part)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if parsed.scheme not in {"http", "https"} or (host != "feishu.cn" and not host.endswith(".feishu.cn")):
            return None
        if len(parts) == 3 and parts[:2] == ("wiki", "space") and _SPACE_ID.fullmatch(parts[2]):
            return parts[2], ""
        if len(parts) == 2 and parts[0] == "wiki" and _WIKI_TOKEN.fullmatch(parts[1]):
            return "", parts[1]
        return None

    def _json(self, argv: Sequence[str], fallback: str, *, stdin: str | None = None) -> tuple[dict[str, Any], AdapterResult | None]:
        response = self._runner.run(argv, stdin=stdin)
        payload = self._error_payload(response.stdout) or self._error_payload(response.stderr)
        if response.returncode != 0:
            return {}, self._failure(payload, fallback)
        if payload is None:
            return {}, AdapterResult.failed(fallback, "Feishu CLI did not return valid JSON.", blocked=fallback in {"permission_denied", "readback_failed"})
        if not isinstance(payload, dict):
            return {}, AdapterResult.failed(fallback, "Feishu CLI returned an invalid JSON envelope.", blocked=True)
        if payload.get("ok") is False:
            return {}, self._failure(payload, fallback)
        data = payload.get("data", payload)
        return (data, None) if isinstance(data, dict) else ({}, AdapterResult.failed(fallback, "Feishu CLI response has no object data.", blocked=True))

    @staticmethod
    def _error_payload(raw: str) -> dict[str, Any] | None:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _failure(payload: dict[str, Any] | None, fallback: str) -> AdapterResult:
        error = payload.get("error") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else {}
        fields = {field: error.get(field, "") for field in ("type", "subtype", "code", "message")}
        markers = {str(fields[field]).lower() for field in ("type", "subtype", "code")}
        if markers & {"permission_denied", "forbidden", "insufficient_scope", "access_denied"}:
            return AdapterResult.failed("permission_denied", "Feishu denied the requested operation.", blocked=True)
        if markers & {"authentication_failed", "authorization_failed", "unauthorized", "access_token_invalid", "user_not_authorized"}:
            return AdapterResult.failed("feishu_auth_missing", "Feishu user authorization is unavailable.", blocked=True)
        return AdapterResult.failed(fallback, "Feishu CLI operation failed.", blocked=fallback in {"permission_denied", "readback_failed"})

    @staticmethod
    def _later_stage(method: str) -> AdapterResult:
        return AdapterResult.failed("format_unsupported", f"{method} is unavailable before its assigned ZSK stage.", blocked=True)

    @staticmethod
    def _wiki_get_node(token: str) -> tuple[str, ...]:
        return ("lark-cli", "--as", "user", "wiki", "spaces", "get_node", "--params", json.dumps({"token": token}, separators=(",", ":")), "--format", "json")

    @staticmethod
    def _wiki_get_space(space_id: str) -> tuple[str, ...]:
        return ("lark-cli", "--as", "user", "wiki", "spaces", "get", "--params", json.dumps({"space_id": space_id}, separators=(",", ":")), "--format", "json")

    @staticmethod
    def _wiki_list(space_id: str, parent: str, page_token: str) -> tuple[str, ...]:
        params = {"space_id": space_id, "page_size": 50}
        if parent:
            params["parent_node_token"] = parent
        if page_token:
            params["page_token"] = page_token
        return ("lark-cli", "--as", "user", "wiki", "nodes", "list", "--params", json.dumps(params, separators=(",", ":")), "--format", "json")

    @staticmethod
    def _wiki_create(space_id: str, data: dict[str, str]) -> tuple[str, ...]:
        return ("lark-cli", "--as", "user", "wiki", "nodes", "create", "--params", json.dumps({"space_id": space_id}, separators=(",", ":")), "--data", json.dumps(data, ensure_ascii=False, separators=(",", ":")), "--format", "json")

    @staticmethod
    def _doc_overwrite(token: str) -> tuple[str, ...]:
        return ("lark-cli", "--as", "user", "docs", "+update", "--api-version", "v2", "--doc", token, "--command", "overwrite", "--doc-format", "markdown", "--content", "-", "--format", "json")

    @staticmethod
    def _doc_fetch(token: str) -> tuple[str, ...]:
        return ("lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", token, "--doc-format", "markdown", "--format", "json")
