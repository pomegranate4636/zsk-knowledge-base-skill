"""ZSK 首次建库：宽松意图、确认预览和飞书/Obsidian 创建。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sqlite3
from typing import Any

from .confirmation_store import ConfirmationStore
from .contracts import BINDING_SCHEMA, ROOT_KEYS, TASK_ID, Binding
from .content_source_contract import (
    ContentSourceContractError,
    build_base_manifest,
    build_empty_profile_index,
    write_feishu_base_contract,
    write_obsidian_base_contract,
)
from .feishu_adapter import FeishuAdapter
from .feishu_cli import CliResponse, CliRunner, SubprocessCliRunner
from .obsidian_adapter import ObsidianAdapter
from .oral_structure_preset import OralStructurePreset, PresetError, load_preset
from .oral_structure_install import install_feishu_preset, install_obsidian_preset
from .stage2_router import RouterRequest, Stage2Router, classify_intent
from .templates import TEMPLATE_VERSION
from .local_permissions import LIBRARY_DIRECTORY_MODE


@dataclass(frozen=True)
class BootstrapRequest:
    task_id: str
    user_input: str
    backend_type: str | None = None
    knowledge_base_name: str | None = None
    client_name: str | None = None
    obsidian_parent: str | None = None
    confirmation: str | None = None

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID")
        if not isinstance(self.user_input, str) or not self.user_input.strip():
            raise ValueError("user_input is required")
        if self.backend_type is not None and self.backend_type not in {"feishu", "obsidian"}:
            raise ValueError("backend_type must be feishu or obsidian")
        if self.knowledge_base_name is not None and (not self.knowledge_base_name.strip() or any(mark in self.knowledge_base_name for mark in ("/", "\\", "\x00"))):
            raise ValueError("knowledge_base_name is unsafe")


@dataclass(frozen=True)
class BootstrapResponse:
    status: str
    code: str | None
    message: str
    preview: dict[str, str]
    confirmation: str | None
    locator: str | None
    root_refs: tuple[Any, ...] = ()


class FirstRunBootstrap:
    """只在用户明确建库并确认预览后创建；失败不回退到隐式默认位置。"""

    def __init__(self, *, runner: CliRunner | None = None, documents_parent: Path | None = None, confirmation_dir: Path | None = None) -> None:
        self.runner = runner or SubprocessCliRunner()
        self.documents_parent = documents_parent
        self.confirmations = ConfirmationStore(confirmation_dir)

    def execute(self, request: BootstrapRequest) -> BootstrapResponse:
        if classify_intent(request.user_input) != "create":
            return BootstrapResponse("needs_input", "routing_ambiguous", "请显式调用 zsk-router 后说明要创建、新建或搭建知识库。", {}, None, None)
        if request.backend_type is None:
            return BootstrapResponse("needs_input", None, "请选择飞书知识库或 Obsidian 本地知识库。", {}, None, None)
        if request.knowledge_base_name is None:
            return BootstrapResponse("needs_input", None, "请提供知识库名称。", {}, None, None)
        name = request.knowledge_base_name.strip()
        client_name = (request.client_name or name).strip()
        preview, code = self._preview(request.backend_type, name, request.obsidian_parent)
        if code:
            messages = {
                "feishu_auth_missing": "请先连接自己的飞书，连接后再继续创建。",
                "binding_missing": "默认 Obsidian 位置不可用，请选择一个可写的父目录。",
                "binding_conflict": "这个目标已存在，请换名称或位置，系统不会覆盖或复用。",
                "readback_failed": "无法确认飞书中是否已有同名知识空间，未创建。",
            }
            return BootstrapResponse("connection_required" if code == "feishu_auth_missing" else "needs_input", code, messages.get(code, "创建前检查未通过，未创建。"), preview, None, None)
        try:
            preset = load_preset()
        except PresetError as exc:
            return BootstrapResponse("blocked", exc.code, str(exc) + " 未创建知识库。", preview, None, None)
        preview = self._preset_preview(preview, preset)
        digest = hashlib.sha256(json.dumps({
            "task": request.task_id, "backend": request.backend_type, "client": client_name,
            "name": name, "target": preview["target"], "template": TEMPLATE_VERSION,
            "preset": preset.sha256, "account": preview.get("account_fingerprint", "local"),
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        try:
            if request.confirmation is None:
                token = self.confirmations.issue(digest)
                return BootstrapResponse("confirmation_required", None, "请确认名称、目标位置及包含完整口播结构库的建库内容；确认在 30 分钟内有效。", preview, token, None)
            code = self.confirmations.consume(request.confirmation, digest)
        except (OSError, sqlite3.Error):
            return BootstrapResponse("blocked", "write_failed", "本机确认记录不可用，未创建。", preview, None, None)
        if code:
            return BootstrapResponse("blocked", code, "确认已失效、已使用或账号/目标发生变化。请重新预览并确认，未创建。", preview, None, None)
        if request.backend_type == "feishu":
            account = FeishuAdapter(self.runner).current_account()
            if account.status != "ok" or account.metadata.get("account_fingerprint") != preview.get("account_fingerprint"):
                return BootstrapResponse("blocked", "confirmation_mismatch", "创建前飞书账号发生变化或无法验证，请重新预览。", preview, None, None)
        if request.backend_type == "obsidian":
            return self._create_obsidian(client_name, name, Path(preview["target"]), preset)
        return self._create_feishu(client_name, name, preset)

    def _preview(self, backend: str, name: str, parent: str | None) -> tuple[dict[str, str], str | None]:
        if backend == "feishu":
            probe = FeishuAdapter(self.runner).doctor()
            if probe.status not in {"ok", "reused"}:
                return {"backend": "feishu", "name": name, "target": "连接自己的飞书后创建"}, probe.code or "feishu_auth_missing"
            existing = self._feishu_name_exists(name)
            if existing is None:
                return {"backend": "feishu", "name": name, "target": "无法确认同名空间"}, "readback_failed"
            if existing:
                return {"backend": "feishu", "name": name, "target": "已有同名私有知识空间"}, "binding_conflict"
            account = FeishuAdapter(self.runner).current_account()
            if account.status != "ok":
                return {"backend": "feishu", "name": name, "target": "当前账号无法验证"}, account.code
            return {"backend": "feishu", "name": name, "target": "将在你的飞书账号下创建私有知识空间",
                    "account_fingerprint": account.metadata["account_fingerprint"],
                    "permission_check": "连接已检查；实际创建和写入由飞书逐项校验权限，失败即停止并报告已创建对象。"}, None
        root = Path(parent) if parent else self._default_documents_parent()
        if not self._safe_directory(root):
            return {"backend": "obsidian", "name": name, "target": str(root)}, "binding_missing"
        target = root / name
        if target.exists() or target.is_symlink():
            return {"backend": "obsidian", "name": name, "target": str(target)}, "binding_conflict"
        return {"backend": "obsidian", "name": name, "target": str(target)}, None

    def _create_obsidian(self, client_name: str, name: str, target: Path, preset: OralStructurePreset) -> BootstrapResponse:
        try:
            os.mkdir(target, LIBRARY_DIRECTORY_MODE)
        except OSError:
            return BootstrapResponse("blocked", "write_failed", "Obsidian 目标目录无法创建。", {"backend": "obsidian", "name": name, "target": str(target)}, None, None)
        binding = Binding(BINDING_SCHEMA, self._client_id(f"obsidian:{target}"), client_name, name, "company", "obsidian", str(target), {key: f"root:{key}" for key in ROOT_KEYS}, TEMPLATE_VERSION)
        result = self._skeleton(ObsidianAdapter(), binding)
        if result.status != "created":
            return BootstrapResponse("blocked", result.code or "write_failed", "Vault 目录已创建，但知识库结构未完整创建。", {"backend": "obsidian", "name": name, "target": str(target)}, None, str(target), result.root_refs)
        try:
            manifest = build_base_manifest(
                client_id=binding.client_id,
                knowledge_base_name=name,
                backend="obsidian",
                locator=str(target.resolve(strict=True)),
            )
            index = build_empty_profile_index(knowledge_base_id=manifest["knowledge_base_id"])
            write_obsidian_base_contract(target, manifest, index)
        except (OSError, ContentSourceContractError) as exc:
            return BootstrapResponse("blocked", "write_failed", f"知识库结构已创建，但基础 Content 清单未完整写入：{exc}", {"backend": "obsidian", "name": name, "target": str(target)}, None, str(target), result.root_refs)
        preview = self._preset_preview({"backend": "obsidian", "name": name, "target": str(target)}, preset)
        try:
            install_obsidian_preset(target, preset)
        except PresetError as exc:
            return BootstrapResponse("blocked", exc.code, "知识库骨架已创建，但口播结构预置未完整完成。" + str(exc), preview, None, str(target), result.root_refs)
        return BootstrapResponse("created", None, "知识库、基础自描述清单和完整口播结构库已创建并回读，可以开始上传资料。", preview, None, str(target), result.root_refs)

    def _create_feishu(self, client_name: str, name: str, preset: OralStructurePreset) -> BootstrapResponse:
        data = {"name": name, "description": "由 ZSK 首次建库创建。", "open_sharing": "closed"}
        response = self.runner.run(("lark-cli", "--as", "user", "wiki", "spaces", "create", "--data", json.dumps(data, ensure_ascii=False, separators=(",", ":")), "--yes", "--format", "json"))
        payload = self._json(response)
        space = payload.get("data", {}).get("space", {}) if isinstance(payload, dict) else {}
        space_id = space.get("space_id") if isinstance(space, dict) else None
        if response.returncode != 0 or not isinstance(space_id, str) or not space_id:
            code = "permission_denied" if self._error_type(payload) in {"permission_denied", "forbidden", "insufficient_scope"} else "feishu_auth_missing" if self._error_type(payload) in {"unauthorized", "authentication_failed"} else "write_failed"
            return BootstrapResponse("blocked", code, "飞书知识空间未创建。", {"backend": "feishu", "name": name, "target": "飞书"}, None, None)
        locator = f"https://feishu.cn/wiki/space/{space_id}"
        binding = Binding(BINDING_SCHEMA, self._client_id(locator), client_name, name, "company", "feishu", locator, {key: f"root:{key}" for key in ROOT_KEYS}, TEMPLATE_VERSION)
        result = self._skeleton(FeishuAdapter(self.runner), binding)
        if result.status != "created":
            return BootstrapResponse("blocked", result.code or "write_failed", "飞书空间已创建，但知识库结构未完整创建。", {"backend": "feishu", "name": name, "target": locator}, None, locator, result.root_refs)
        try:
            write_feishu_base_contract(
                self.runner,
                space_id=space_id,
                locator=locator,
                client_id=binding.client_id,
                knowledge_base_name=name,
            )
        except ContentSourceContractError as exc:
            return BootstrapResponse("blocked", "write_failed", f"飞书结构已创建，但基础 Content 清单未完整写入：{exc}", {"backend": "feishu", "name": name, "target": locator}, None, locator, result.root_refs)
        preview = self._preset_preview({"backend": "feishu", "name": name, "target": locator}, preset)
        try:
            install_feishu_preset(self.runner, space_id, preset)
        except PresetError as exc:
            return BootstrapResponse("blocked", exc.code, "飞书知识库骨架已创建，但口播结构预置未完整完成。" + str(exc), preview, None, locator, result.root_refs)
        return BootstrapResponse("created", None, "知识库、基础自描述清单和完整口播结构库已创建并回读，可以开始上传资料。", preview, None, locator, result.root_refs)

    @staticmethod
    def _preset_preview(preview: dict[str, str], preset: OralStructurePreset) -> dict[str, str]:
        return {**preview, "included_content": f"04-内容方法库/口播结构：完整目录与 {len(preset.documents)} 份文档；每库独立副本",
                "preset_version": preset.version, "preset_sha256": preset.sha256, "template_version": TEMPLATE_VERSION}

    def _feishu_name_exists(self, name: str) -> bool | None:
        response = self.runner.run(("lark-cli", "--as", "user", "wiki", "spaces", "list", "--page-all", "--format", "json"))
        payload = self._json(response)
        items = payload.get("data", {}).get("items") if isinstance(payload, dict) else None
        if response.returncode != 0 or not isinstance(items, list):
            return None
        return any(isinstance(item, dict) and item.get("name") == name for item in items)

    @staticmethod
    def _skeleton(adapter: Any, binding: Binding) -> Any:
        router = Stage2Router(adapter)
        first = router.execute(RouterRequest("01a01e29-a6ba-73a2-82e6-4ad1caa0f33b", "创建知识库", binding.backend_type, binding.backend_locator, binding.client_name, binding.knowledge_base_name, binding.subject_type))
        if first.status != "confirmation_required" or first.confirmation is None:
            return first
        return router.execute(RouterRequest("01a01e29-a6ba-73a2-82e6-4ad1caa0f33b", "创建知识库", binding.backend_type, binding.backend_locator, binding.client_name, binding.knowledge_base_name, binding.subject_type, first.confirmation))

    def _default_documents_parent(self) -> Path:
        if self.documents_parent is not None:
            return self.documents_parent
        home = Path.home()
        return home / "Documents"

    @staticmethod
    def _safe_directory(path: Path) -> bool:
        if not path.is_absolute() or ".." in path.parts:
            return False
        try:
            current = Path(path.anchor)
            for part in path.parts[1:]:
                current /= part
                if stat.S_ISLNK(os.lstat(current).st_mode):
                    return False
            return stat.S_ISDIR(os.lstat(path).st_mode)
        except OSError:
            return False

    @staticmethod
    def _client_id(locator: str) -> str:
        return "CLT-" + hashlib.sha256(locator.encode()).hexdigest()[:14].upper()

    @staticmethod
    def _json(response: CliResponse) -> dict[str, Any]:
        raw = response.stdout if "{" in response.stdout else response.stderr
        try:
            return json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _error_type(payload: dict[str, Any]) -> str:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        return str(error.get("type") or error.get("code") or "").lower() if isinstance(error, dict) else ""
