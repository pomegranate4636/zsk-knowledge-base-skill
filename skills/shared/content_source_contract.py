"""Versioned, backend-neutral contract shared with Content workflows.

ZSK is the canonical schema owner.  Content products carry compatible validator
copies and never import this module at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit


CONTRACT_VERSION = "content-source-v1"
REGISTRY_RELATIVE_PATH = Path(".content-workflows") / "knowledge-base-registry.json"
MANIFEST_RELATIVE_PATH = PurePosixPath("06-Agent与Workflow/content-source-manifest.json")
PROFILE_INDEX_RELATIVE_PATH = PurePosixPath("06-Agent与Workflow/content-profile-index.json")
SUPPORTED_BACKENDS = frozenset({"obsidian", "feishu"})
SUPPORTED_WORKFLOWS = frozenset({"content-koubo-slim", "content-gzh-slim"})
PROFILE_ID_PATTERN = re.compile(r"^PRF-[A-F0-9]{16}$")
BINDING_ID_PATTERN = re.compile(r"^BND-[A-F0-9]{16}$")
KNOWLEDGE_BASE_ID_PATTERN = re.compile(r"^KB-[A-F0-9]{16}$")
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL_KEYS = frozenset(
    {"token", "cookie", "password", "access_token", "refresh_token", "secret", "api_key", "apikey", "authorization", "credential", "session"}
)


class ContentSourceContractError(ValueError):
    """Fail closed when a shared Content source contract is unsafe or ambiguous."""


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\n".join(value.strip() for value in values).encode("utf-8")).hexdigest()[:16].upper()
    return f"{prefix}-{digest}"


def stable_knowledge_base_id(backend: str, locator: str) -> str:
    return stable_id("KB", backend, locator)


def stable_binding_id(client_id: str, knowledge_base_id: str) -> str:
    return stable_id("BND", client_id, knowledge_base_id)


def stable_profile_id(client_id: str, subject_name: str) -> str:
    return stable_id("PRF", client_id, subject_name.casefold())


def default_registry_path() -> Path:
    configured = os.environ.get("CODEX_HOME")
    root = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return root / REGISTRY_RELATIVE_PATH


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentSourceContractError(f"{field} must be a non-empty string")
    return value.strip()


def _no_credentials(value: Any, field: str = "locator") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _CREDENTIAL_KEYS:
                raise ContentSourceContractError(f"{field} must not contain credentials")
            _no_credentials(child, field)
        return
    if isinstance(value, list):
        for child in value:
            _no_credentials(child, field)
        return
    if isinstance(value, str):
        try:
            parsed = urlsplit(value)
            pairs = (*parse_qsl(parsed.query, keep_blank_values=True), *parse_qsl(parsed.fragment, keep_blank_values=True))
        except ValueError as exc:
            raise ContentSourceContractError(f"{field} is invalid") from exc
        if any(key.casefold().replace("-", "_") in _CREDENTIAL_KEYS for key, _ in pairs):
            raise ContentSourceContractError(f"{field} must not contain credentials")


def _relative(value: Any, field: str) -> str:
    text = _non_empty(value, field)
    path = PurePosixPath(text)
    if path.is_absolute() or "\\" in text or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ContentSourceContractError(f"{field} must stay below the knowledge-base root")
    return path.as_posix()


def build_base_manifest(
    *,
    client_id: str,
    knowledge_base_name: str,
    backend: str,
    locator: str,
    root_refs: Mapping[str, str] | None = None,
    profile_index_ref: str | None = None,
) -> dict[str, Any]:
    if not CLIENT_ID_PATTERN.fullmatch(client_id):
        raise ContentSourceContractError("client_id is invalid")
    if backend not in SUPPORTED_BACKENDS:
        raise ContentSourceContractError("backend is unsupported")
    _no_credentials(locator)
    knowledge_base_id = stable_knowledge_base_id(backend, locator)
    if backend == "obsidian":
        asset_roots = {
            "knowledge": "03-业务知识库",
            "content": "04-内容方法库",
            "profiles": "05-IP-Profile",
            "workflow": "06-Agent与Workflow",
            "output": "07-生产与反馈",
        }
        resolved_profile_index_ref = PROFILE_INDEX_RELATIVE_PATH.as_posix()
    else:
        refs = dict(root_refs or {})
        required = {"03", "04", "05", "06", "07"}
        if set(refs) != required or any(not isinstance(value, str) or not value.strip() for value in refs.values()):
            raise ContentSourceContractError("Feishu base manifest requires stable 03-07 object refs")
        asset_roots = {
            "knowledge": refs["03"],
            "content": refs["04"],
            "profiles": refs["05"],
            "workflow": refs["06"],
            "output": refs["07"],
        }
        resolved_profile_index_ref = _non_empty(profile_index_ref or "content-profile-index", "profile_index_ref")
    value = {
        "contract_version": CONTRACT_VERSION,
        "knowledge_base_id": knowledge_base_id,
        "client_id": client_id,
        "knowledge_base_name": _non_empty(knowledge_base_name, "knowledge_base_name"),
        "backend": backend,
        "locator": locator,
        "asset_roots": asset_roots,
        "profile_index_ref": resolved_profile_index_ref,
        "workflow_outputs": {
            "content-koubo-slim": "content-koubo-slim/{profile_id}/weekly",
            "content-gzh-slim": "content-gzh-slim/{profile_id}/articles",
        },
        "supported_workflows": sorted(SUPPORTED_WORKFLOWS),
        "revision": 1,
    }
    validate_manifest(value)
    return value


def build_empty_profile_index(*, knowledge_base_id: str) -> dict[str, Any]:
    value = {
        "contract_version": CONTRACT_VERSION,
        "knowledge_base_id": knowledge_base_id,
        "profiles": [],
        "revision": 1,
    }
    validate_profile_index(value)
    return value


def validate_manifest(value: Any) -> dict[str, Any]:
    fields = {
        "contract_version", "knowledge_base_id", "client_id", "knowledge_base_name", "backend", "locator",
        "asset_roots", "profile_index_ref", "workflow_outputs", "supported_workflows", "revision",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ContentSourceContractError("manifest fields are invalid")
    if value["contract_version"] != CONTRACT_VERSION:
        raise ContentSourceContractError("manifest version is unsupported")
    if not KNOWLEDGE_BASE_ID_PATTERN.fullmatch(_non_empty(value["knowledge_base_id"], "knowledge_base_id")):
        raise ContentSourceContractError("knowledge_base_id is invalid")
    if not CLIENT_ID_PATTERN.fullmatch(_non_empty(value["client_id"], "client_id")):
        raise ContentSourceContractError("client_id is invalid")
    _non_empty(value["knowledge_base_name"], "knowledge_base_name")
    backend = value["backend"]
    if backend not in SUPPORTED_BACKENDS:
        raise ContentSourceContractError("backend is unsupported")
    locator = _non_empty(value["locator"], "locator")
    _no_credentials(locator)
    if stable_knowledge_base_id(backend, locator) != value["knowledge_base_id"]:
        raise ContentSourceContractError("knowledge_base_id does not match backend locator")
    roots = value["asset_roots"]
    if not isinstance(roots, dict) or set(roots) != {"knowledge", "content", "profiles", "workflow", "output"}:
        raise ContentSourceContractError("manifest asset_roots are invalid")
    if backend == "obsidian":
        for key, child in roots.items():
            _relative(child, f"asset_roots.{key}")
        _relative(value["profile_index_ref"], "profile_index_ref")
    else:
        for child in roots.values():
            _non_empty(child, "Feishu asset root ref")
            _no_credentials(child)
        _non_empty(value["profile_index_ref"], "profile_index_ref")
    outputs = value["workflow_outputs"]
    if not isinstance(outputs, dict) or set(outputs) - SUPPORTED_WORKFLOWS:
        raise ContentSourceContractError("workflow_outputs are invalid")
    for key, child in outputs.items():
        _relative(child, f"workflow_outputs.{key}")
        if "{profile_id}" not in child:
            raise ContentSourceContractError("workflow output template must contain {profile_id}")
    workflows = value["supported_workflows"]
    if not isinstance(workflows, list) or len(workflows) != len(set(workflows)) or set(workflows) - SUPPORTED_WORKFLOWS:
        raise ContentSourceContractError("supported_workflows are invalid")
    if not isinstance(value["revision"], int) or value["revision"] < 1:
        raise ContentSourceContractError("manifest revision is invalid")
    return value


def validate_profile_index(value: Any, *, expected_knowledge_base_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"contract_version", "knowledge_base_id", "profiles", "revision"}:
        raise ContentSourceContractError("profile index fields are invalid")
    if value["contract_version"] != CONTRACT_VERSION:
        raise ContentSourceContractError("profile index version is unsupported")
    knowledge_base_id = _non_empty(value["knowledge_base_id"], "knowledge_base_id")
    if not KNOWLEDGE_BASE_ID_PATTERN.fullmatch(knowledge_base_id) or expected_knowledge_base_id not in {None, knowledge_base_id}:
        raise ContentSourceContractError("profile index belongs to another knowledge base")
    profiles = value["profiles"]
    if not isinstance(profiles, list):
        raise ContentSourceContractError("profiles must be a list")
    seen_ids: set[str] = set()
    active_primary = 0
    active_names: dict[str, str] = {}
    for item in profiles:
        required = {"profile_id", "display_name", "aliases", "object_ref", "status", "is_primary", "content_sha256"}
        if not isinstance(item, dict) or set(item) != required:
            raise ContentSourceContractError("profile index entry fields are invalid")
        profile_id = _non_empty(item["profile_id"], "profile_id")
        if not PROFILE_ID_PATTERN.fullmatch(profile_id) or profile_id in seen_ids:
            raise ContentSourceContractError("profile_id is invalid or duplicated")
        seen_ids.add(profile_id)
        name = _non_empty(item["display_name"], "display_name")
        aliases = item["aliases"]
        if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases) or len(aliases) != len(set(alias.casefold() for alias in aliases)):
            raise ContentSourceContractError("profile aliases are invalid")
        if item["status"] not in {"active", "disabled"} or not isinstance(item["is_primary"], bool):
            raise ContentSourceContractError("profile status or primary flag is invalid")
        _non_empty(item["object_ref"], "profile object_ref")
        digest = item["content_sha256"]
        if not isinstance(digest, str) or not HEX64.fullmatch(digest):
            raise ContentSourceContractError("profile content_sha256 is invalid")
        if item["status"] == "active" and item["is_primary"]:
            active_primary += 1
        if item["status"] == "active":
            for candidate in (name, *aliases):
                folded = candidate.casefold()
                previous = active_names.get(folded)
                if previous is not None and previous != profile_id:
                    raise ContentSourceContractError("active profile name or alias is ambiguous")
                active_names[folded] = profile_id
    if active_primary > 1:
        raise ContentSourceContractError("at most one active Profile may be primary")
    if not isinstance(value["revision"], int) or value["revision"] < 1:
        raise ContentSourceContractError("profile index revision is invalid")
    return value


def validate_registry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"contract_version", "bindings", "workflow_defaults", "revision"}:
        raise ContentSourceContractError("registry fields are invalid")
    if value["contract_version"] != CONTRACT_VERSION:
        raise ContentSourceContractError("registry version is unsupported")
    bindings = value["bindings"]
    if not isinstance(bindings, dict):
        raise ContentSourceContractError("registry bindings must be an object")
    for binding_id, binding in bindings.items():
        required = {
            "binding_id", "client_id", "knowledge_base_id", "backend", "locator", "manifest_ref",
            "profile_index_ref", "supported_workflows", "workflow_defaults", "status",
        }
        if not BINDING_ID_PATTERN.fullmatch(str(binding_id)) or not isinstance(binding, dict) or set(binding) != required or binding.get("binding_id") != binding_id:
            raise ContentSourceContractError("registry binding identity or fields are invalid")
        if not CLIENT_ID_PATTERN.fullmatch(_non_empty(binding["client_id"], "client_id")):
            raise ContentSourceContractError("registry client_id is invalid")
        if not KNOWLEDGE_BASE_ID_PATTERN.fullmatch(_non_empty(binding["knowledge_base_id"], "knowledge_base_id")):
            raise ContentSourceContractError("registry knowledge_base_id is invalid")
        if stable_binding_id(binding["client_id"], binding["knowledge_base_id"]) != binding_id:
            raise ContentSourceContractError("binding_id does not match client and knowledge base")
        if binding["backend"] not in SUPPORTED_BACKENDS:
            raise ContentSourceContractError("registry backend is unsupported")
        if not isinstance(binding["locator"], dict) or not binding["locator"]:
            raise ContentSourceContractError("registry locator is invalid")
        _no_credentials(binding["locator"])
        _non_empty(binding["manifest_ref"], "manifest_ref")
        _non_empty(binding["profile_index_ref"], "profile_index_ref")
        workflows = binding["supported_workflows"]
        if not isinstance(workflows, list) or len(workflows) != len(set(workflows)) or set(workflows) - SUPPORTED_WORKFLOWS:
            raise ContentSourceContractError("binding workflows are invalid")
        defaults = binding["workflow_defaults"]
        if not isinstance(defaults, dict) or set(defaults) - set(workflows):
            raise ContentSourceContractError("binding workflow defaults are invalid")
        for workflow, default in defaults.items():
            if not isinstance(default, dict) or set(default) != {"profile_id", "use_no_ip"}:
                raise ContentSourceContractError(f"{workflow} default is invalid")
            profile_id = default["profile_id"]
            if profile_id is not None and not PROFILE_ID_PATTERN.fullmatch(str(profile_id)):
                raise ContentSourceContractError("default profile_id is invalid")
            if not isinstance(default["use_no_ip"], bool) or default["use_no_ip"] and profile_id is not None:
                raise ContentSourceContractError("workflow default IP policy is invalid")
        if binding["status"] not in {"active", "disabled"}:
            raise ContentSourceContractError("binding status is invalid")
    defaults = value["workflow_defaults"]
    if not isinstance(defaults, dict) or set(defaults) - SUPPORTED_WORKFLOWS:
        raise ContentSourceContractError("workflow_defaults are invalid")
    for workflow, binding_id in defaults.items():
        if binding_id not in bindings or workflow not in bindings[binding_id]["supported_workflows"]:
            raise ContentSourceContractError("workflow default points to an incompatible binding")
    if not isinstance(value["revision"], int) or value["revision"] < 1:
        raise ContentSourceContractError("registry revision is invalid")
    return value


@dataclass(frozen=True)
class RegistryPlan:
    registry_path: Path
    registry: dict[str, Any]
    binding_id: str
    preview_sha256: str
    confirmation: str
    action: str
    registry_before_sha256: str | None


def plan_registry_binding(
    *,
    manifest: Mapping[str, Any],
    manifest_ref: str,
    profile_index_ref: str,
    workflows: tuple[str, ...],
    registry_path: str | Path | None = None,
    default_profiles: Mapping[str, str | None] | None = None,
) -> RegistryPlan:
    checked_manifest = validate_manifest(dict(manifest))
    if not workflows or len(workflows) != len(set(workflows)) or set(workflows) - set(checked_manifest["supported_workflows"]):
        raise ContentSourceContractError("requested workflows are invalid")
    path = Path(registry_path) if registry_path is not None else default_registry_path()
    if not path.is_absolute():
        raise ContentSourceContractError("registry path must be absolute")
    existing = {"contract_version": CONTRACT_VERSION, "bindings": {}, "workflow_defaults": {}, "revision": 1}
    registry_before_sha256 = None
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ContentSourceContractError("registry path is not a regular file")
        try:
            raw = path.read_bytes()
            existing = json.loads(raw.decode("utf-8"))
            registry_before_sha256 = hashlib.sha256(raw).hexdigest()
        except (OSError, json.JSONDecodeError) as exc:
            raise ContentSourceContractError("registry is unreadable") from exc
        validate_registry(existing)
    knowledge_base_id = checked_manifest["knowledge_base_id"]
    binding_id = stable_binding_id(checked_manifest["client_id"], knowledge_base_id)
    if checked_manifest["backend"] == "obsidian":
        locator = {"vault_root": checked_manifest["locator"]}
    else:
        locator = {"knowledge_base_ref": checked_manifest["locator"]}
    defaults = dict(default_profiles or {})
    binding = {
        "binding_id": binding_id,
        "client_id": checked_manifest["client_id"],
        "knowledge_base_id": knowledge_base_id,
        "backend": checked_manifest["backend"],
        "locator": locator,
        "manifest_ref": _non_empty(manifest_ref, "manifest_ref"),
        "profile_index_ref": _non_empty(profile_index_ref, "profile_index_ref"),
        "supported_workflows": sorted(workflows),
        "workflow_defaults": {workflow: {"profile_id": defaults.get(workflow), "use_no_ip": False} for workflow in sorted(workflows)},
        "status": "active",
    }
    updated = json.loads(json.dumps(existing))
    previous = updated["bindings"].get(binding_id)
    if previous is not None and previous != binding:
        raise ContentSourceContractError("an existing binding has different content")
    action = "reused" if previous == binding else "create" if previous is None else "update"
    updated["bindings"][binding_id] = binding
    for workflow in workflows:
        current = updated["workflow_defaults"].get(workflow)
        if current not in {None, binding_id}:
            # Multiple bindings are allowed, but changing a default must be explicit.
            continue
        updated["workflow_defaults"][workflow] = binding_id
    if updated != existing:
        updated["revision"] = int(existing.get("revision", 0)) + 1
    validate_registry(updated)
    preview = {
        "action": action,
        "registry_path": str(path),
        "binding": binding,
        "workflow_defaults_after": updated["workflow_defaults"],
        "wrote": False,
    }
    digest = sha256_json(preview)
    confirmation = hashlib.sha256(("content-source-v1-confirm\0" + digest).encode("utf-8")).hexdigest()[:24]
    return RegistryPlan(path, updated, binding_id, digest, confirmation, action, registry_before_sha256)


def commit_registry_plan(plan: RegistryPlan, confirmation: str) -> dict[str, Any]:
    if confirmation != plan.confirmation:
        raise ContentSourceContractError("confirmation does not match the zero-write preview")
    current = None
    if plan.registry_path.exists():
        if plan.registry_path.is_symlink() or not plan.registry_path.is_file():
            raise ContentSourceContractError("registry path changed after preview")
        raw = plan.registry_path.read_bytes()
        if plan.registry_before_sha256 is None or hashlib.sha256(raw).hexdigest() != plan.registry_before_sha256:
            raise ContentSourceContractError("registry changed after preview")
        current = json.loads(raw.decode("utf-8"))
        validate_registry(current)
    elif plan.registry_before_sha256 is not None:
        raise ContentSourceContractError("registry disappeared after preview")
    plan.registry_path.parent.mkdir(parents=True, exist_ok=True)
    if plan.registry_path.parent.is_symlink():
        raise ContentSourceContractError("registry parent must not be a symlink")
    payload = _canonical(plan.registry)
    temporary = plan.registry_path.with_name(f".{plan.registry_path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, plan.registry_path)
        if plan.registry_path.read_bytes() != payload:
            raise ContentSourceContractError("registry readback mismatch")
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"status": "configured", "binding_id": plan.binding_id, "registry_path": str(plan.registry_path), "readback": "verified"}


def write_obsidian_base_contract(vault_root: str | Path, manifest: Mapping[str, Any], profile_index: Mapping[str, Any]) -> tuple[Path, Path]:
    checked_manifest = validate_manifest(dict(manifest))
    checked_index = validate_profile_index(dict(profile_index), expected_knowledge_base_id=checked_manifest["knowledge_base_id"])
    root = Path(vault_root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ContentSourceContractError("Obsidian vault root is invalid")
    root = root.resolve(strict=True)
    manifest_path = root.joinpath(*MANIFEST_RELATIVE_PATH.parts)
    index_path = root.joinpath(*PROFILE_INDEX_RELATIVE_PATH.parts)
    for path, value in ((manifest_path, checked_manifest), (index_path, checked_index)):
        path.parent.resolve(strict=True).relative_to(root)
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != _canonical(value):
                raise ContentSourceContractError(f"existing {path.name} differs; refusing to overwrite")
            continue
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(value))
        if path.read_bytes() != _canonical(value):
            raise ContentSourceContractError(f"{path.name} readback mismatch")
    return manifest_path, index_path


def _cli_json(response: Any) -> dict[str, Any]:
    raw = getattr(response, "stdout", "") or getattr(response, "stderr", "")
    decoder = json.JSONDecoder()
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value.get("data", value) if isinstance(value.get("data", value), dict) else value
    raise ContentSourceContractError("Feishu CLI returned no JSON object")


def _feishu_document_payload(title: str, value: Mapping[str, Any]) -> str:
    body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return f"# {title}\n\n```json\n{body}\n```\n"


def _feishu_create_json_document(runner: Any, *, space_id: str, parent_node_token: str, title: str, value: Mapping[str, Any]) -> str:
    listed = runner.run((
        "lark-cli", "--as", "user", "wiki", "nodes", "list", "--space-id", space_id,
        "--parent-node-token", parent_node_token, "--page-all", "--format", "json",
    ))
    data = _cli_json(listed)
    items = data.get("items") or []
    if getattr(listed, "returncode", 1) != 0 or not isinstance(items, list):
        raise ContentSourceContractError("Feishu 06 child list cannot be read")
    matches = [item for item in items if isinstance(item, dict) and item.get("title") == title]
    if len(matches) > 1:
        raise ContentSourceContractError(f"Feishu 06 contains duplicate {title} objects")
    payload = _feishu_document_payload(title, value)
    if matches:
        token = matches[0].get("obj_token")
        if not isinstance(token, str) or not token:
            raise ContentSourceContractError(f"Feishu {title} has no stable object token")
        fetched = runner.run((
            "lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", token,
            "--doc-format", "markdown", "--format", "json",
        ))
        document = _cli_json(fetched).get("document", {})
        if getattr(fetched, "returncode", 1) != 0 or not isinstance(document, dict) or str(document.get("content", "")).strip() != payload.strip():
            raise ContentSourceContractError(f"existing Feishu {title} differs; refusing to overwrite")
        return token
    created = runner.run((
        "lark-cli", "--as", "user", "wiki", "nodes", "create", "--params",
        json.dumps({"space_id": space_id}, separators=(",", ":")), "--data",
        json.dumps({"obj_type": "docx", "node_type": "origin", "title": title, "parent_node_token": parent_node_token}, ensure_ascii=False, separators=(",", ":")),
        "--format", "json",
    ))
    node = _cli_json(created).get("node", {})
    token = node.get("obj_token") if isinstance(node, dict) else None
    if getattr(created, "returncode", 1) != 0 or not isinstance(token, str) or not token:
        raise ContentSourceContractError(f"Feishu {title} could not be created")
    updated = runner.run((
        "lark-cli", "--as", "user", "docs", "+update", "--api-version", "v2", "--doc", token,
        "--command", "overwrite", "--doc-format", "markdown", "--content", "-", "--format", "json",
    ), stdin=payload)
    if getattr(updated, "returncode", 1) != 0:
        raise ContentSourceContractError(f"Feishu {title} content could not be written")
    fetched = runner.run((
        "lark-cli", "--as", "user", "docs", "+fetch", "--api-version", "v2", "--doc", token,
        "--doc-format", "markdown", "--format", "json",
    ))
    document = _cli_json(fetched).get("document", {})
    if getattr(fetched, "returncode", 1) != 0 or not isinstance(document, dict) or str(document.get("content", "")).strip() != payload.strip():
        raise ContentSourceContractError(f"Feishu {title} readback mismatch")
    return token


def write_feishu_base_contract(
    runner: Any,
    *,
    space_id: str,
    locator: str,
    client_id: str,
    knowledge_base_name: str,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Create the two neutral JSON contract documents below the Feishu 06 root."""

    listed = runner.run((
        "lark-cli", "--as", "user", "wiki", "nodes", "list", "--space-id", space_id,
        "--page-all", "--format", "json",
    ))
    data = _cli_json(listed)
    items = data.get("items") or []
    if getattr(listed, "returncode", 1) != 0 or not isinstance(items, list):
        raise ContentSourceContractError("Feishu roots cannot be listed for the base contract")
    titles = {
        "03": "03-业务知识库", "04": "04-内容方法库", "05": "05-IP-Profile",
        "06": "06-Agent与Workflow", "07": "07-生产与反馈",
    }
    roots: dict[str, str] = {}
    for key, title in titles.items():
        matches = [item for item in items if isinstance(item, dict) and item.get("title") == title]
        if len(matches) != 1 or not isinstance(matches[0].get("node_token"), str):
            raise ContentSourceContractError(f"Feishu {key} root is missing or ambiguous")
        roots[key] = matches[0]["node_token"]
    knowledge_base_id = stable_knowledge_base_id("feishu", locator)
    profile_index = build_empty_profile_index(knowledge_base_id=knowledge_base_id)
    index_ref = _feishu_create_json_document(
        runner,
        space_id=space_id,
        parent_node_token=roots["06"],
        title="content-profile-index",
        value=profile_index,
    )
    manifest = build_base_manifest(
        client_id=client_id,
        knowledge_base_name=knowledge_base_name,
        backend="feishu",
        locator=locator,
        root_refs={key: roots[key] for key in ("03", "04", "05", "06", "07")},
        profile_index_ref=index_ref,
    )
    manifest_ref = _feishu_create_json_document(
        runner,
        space_id=space_id,
        parent_node_token=roots["06"],
        title="content-source-manifest",
        value=manifest,
    )
    return manifest, profile_index, manifest_ref, index_ref


def _profile_entry_from_asset(asset: Any, *, object_ref: str, content_sha256: str) -> dict[str, Any]:
    metadata = getattr(asset, "metadata", {})
    profile_id = getattr(asset, "asset_id", "")
    title = getattr(asset, "title", "")
    display_name = title.removesuffix(" Profile").strip()
    aliases = metadata.get("aliases", ()) if isinstance(metadata, Mapping) else ()
    entry = {
        "profile_id": profile_id,
        "display_name": display_name,
        "aliases": list(aliases),
        "object_ref": object_ref,
        "status": metadata.get("profile_status", "active"),
        "is_primary": metadata.get("is_primary") is True,
        "content_sha256": content_sha256,
    }
    return entry


def _merged_profile_index(index: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    current = validate_profile_index(json.loads(json.dumps(index)))
    matches = [item for item in current["profiles"] if item["profile_id"] == entry["profile_id"]]
    if matches and matches[0] != entry:
        raise ContentSourceContractError("existing Profile index entry differs")
    if not matches:
        current["profiles"].append(entry)
        current["profiles"].sort(key=lambda item: item["profile_id"])
        current["revision"] += 1
    validate_profile_index(current)
    return current


def preflight_obsidian_profile_index(vault_root: Path, asset: Any) -> None:
    index_path = vault_root / PROFILE_INDEX_RELATIVE_PATH.as_posix()
    manifest_path = vault_root / MANIFEST_RELATIVE_PATH.as_posix()
    if not index_path.exists() and not manifest_path.exists():
        return
    if not index_path.is_file() or index_path.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise ContentSourceContractError("base Content contract is incomplete")
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    index = validate_profile_index(json.loads(index_path.read_text(encoding="utf-8")), expected_knowledge_base_id=manifest["knowledge_base_id"])
    placeholder = _profile_entry_from_asset(asset, object_ref="05-IP-Profile/pending.md", content_sha256=hashlib.sha256(asset.body.encode("utf-8")).hexdigest())
    existing = [item for item in index["profiles"] if item["profile_id"] == placeholder["profile_id"]]
    if existing:
        placeholder["object_ref"] = existing[0]["object_ref"]
    _merged_profile_index(index, placeholder)


def sync_obsidian_profile_index(vault_root: Path, asset: Any) -> None:
    index_path = vault_root / PROFILE_INDEX_RELATIVE_PATH.as_posix()
    manifest_path = vault_root / MANIFEST_RELATIVE_PATH.as_posix()
    if not index_path.exists() and not manifest_path.exists():
        return
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    index = validate_profile_index(json.loads(index_path.read_text(encoding="utf-8")), expected_knowledge_base_id=manifest["knowledge_base_id"])
    digest = hashlib.sha256(asset.body.encode("utf-8")).hexdigest()
    candidates = []
    profile_root = vault_root / "05-IP-Profile"
    for path in sorted(profile_root.rglob("*.md")):
        if not path.is_symlink() and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            candidates.append(path)
    if len(candidates) != 1:
        raise ContentSourceContractError("written Profile cannot be resolved uniquely for the index")
    entry = _profile_entry_from_asset(asset, object_ref=candidates[0].relative_to(vault_root).as_posix(), content_sha256=digest)
    updated = _merged_profile_index(index, entry)
    if updated == index:
        return
    payload = _canonical(updated)
    temporary = index_path.with_name(f".{index_path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, index_path)
        if index_path.read_bytes() != payload:
            raise ContentSourceContractError("Profile index readback mismatch")
    finally:
        if temporary.exists():
            temporary.unlink()
