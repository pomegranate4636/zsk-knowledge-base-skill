"""Prepare one confirmed Obsidian knowledge-base handoff for Content 口播 Slim."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from .contracts import CLIENT_ID, Binding
from .templates import ROOT_TITLES


CONTENT_CONTRACT_VERSION = "2.0"
SUPPORTED_SPEAKER_MODES = ("personal_ip", "company_brand", "neutral")
CONTENT_ASSET_ROOTS = {
    "knowledge": ROOT_TITLES["03"],
    "method": ROOT_TITLES["04"],
    "profile": ROOT_TITLES["05"],
    "output": ROOT_TITLES["07"],
}
CONTENT_MANIFEST_RELATIVE_PATH = (
    f"{ROOT_TITLES['06']}/content-koubo-client-manifest.json"
)
CONTENT_OUTPUT_TEMPLATE = "content-koubo-slim/{profile_or_brand}/weekly"
MAX_FRONTMATTER_BYTES = 64 * 1024


class ContentKouboSlimHandoffError(ValueError):
    """Stop before changing a handoff when its identity or paths are unclear."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ContentKouboSlimHandoffPlan:
    client_id: str
    vault_root: Path
    registry_path: Path
    runs_root: Path
    manifest_path: Path
    manifest_relative_path: str
    speaker_mode: str
    registry_action: str
    manifest_action: str
    runs_action: str
    compatible_method_count: int
    skipped_method_count: int
    primary_profile_count: int
    expected_manifest: dict[str, Any]
    expected_registry: dict[str, Any]
    registry_before_sha256: str | None
    preview_sha256: str

    @property
    def needs_write(self) -> bool:
        return any(
            action in {"create", "merge"}
            for action in (
                self.registry_action,
                self.manifest_action,
                self.runs_action,
            )
        )


def default_content_config_root() -> Path:
    configured = os.environ.get("CODEX_HOME")
    host_root = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return host_root / ".content-koubo-slim"


def default_content_registry_path() -> Path:
    return default_content_config_root() / "client-registry.json"


def default_content_runs_root() -> Path:
    return default_content_config_root() / "runs"


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ContentKouboSlimHandoffError(
            "path_unreadable", f"无法检查路径：{path}"
        ) from exc


def _check_existing_chain(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        info = _lstat(current)
        if info is None:
            break
        if stat.S_ISLNK(info.st_mode):
            raise ContentKouboSlimHandoffError(
                "symlink_rejected", f"{label}路径包含软链接：{current}"
            )
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise ContentKouboSlimHandoffError(
                "path_conflict", f"{label}路径经过非目录对象：{current}"
            )


def _absolute_path(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise ContentKouboSlimHandoffError(
            "unsafe_path", f"{label}必须是明确、安全的绝对路径。"
        )
    _check_existing_chain(path, label)
    return path


def _existing_directory(value: str | Path, label: str) -> Path:
    path = _absolute_path(value, label)
    info = _lstat(path)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise ContentKouboSlimHandoffError(
            "directory_missing", f"{label}不存在或不是目录：{path}"
        )
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ContentKouboSlimHandoffError(
            "path_unreadable", f"{label}无法回读：{path}"
        ) from exc


def _file_target(value: str | Path, label: str) -> Path:
    path = _absolute_path(value, label)
    if path.suffix.casefold() != ".json":
        raise ContentKouboSlimHandoffError(
            "unsafe_path", f"{label}必须是 JSON 文件路径。"
        )
    _check_existing_chain(path.parent, label)
    info = _lstat(path)
    if info is not None and not stat.S_ISREG(info.st_mode):
        raise ContentKouboSlimHandoffError(
            "path_conflict", f"{label}已被非普通文件占用：{path}"
        )
    return path


def _is_below(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_relative_json(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix.casefold() != ".json"
        or not path.parts
        or path.parts[0] != ROOT_TITLES["06"]
    ):
        raise ContentKouboSlimHandoffError(
            "manifest_path_invalid",
            f"Manifest 必须位于 {ROOT_TITLES['06']} 下。",
        )
    return path.as_posix()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _read_regular_bytes(path: Path, label: str) -> bytes:
    info = _lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode):
        raise ContentKouboSlimHandoffError(
            "file_missing", f"{label}不存在或不是普通文件：{path}"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ContentKouboSlimHandoffError(
            "file_unreadable", f"{label}无法读取：{path}"
        ) from exc


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular_bytes(path, label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContentKouboSlimHandoffError(
            "file_invalid", f"{label}不是有效 UTF-8 JSON：{path}"
        ) from exc
    if not isinstance(value, dict):
        raise ContentKouboSlimHandoffError(
            "file_invalid", f"{label}必须是 JSON 对象：{path}"
        )
    return value, raw


def _validate_registry(value: dict[str, Any]) -> None:
    if set(value) != {"registry_version", "clients"}:
        raise ContentKouboSlimHandoffError(
            "registry_invalid", "现有 Registry 字段不符合 Content 口播 Slim 合同。"
        )
    clients = value.get("clients")
    if value.get("registry_version") != CONTENT_CONTRACT_VERSION or not isinstance(clients, dict) or not clients:
        raise ContentKouboSlimHandoffError(
            "registry_invalid", "现有 Registry 版本或客户记录无效。"
        )
    for client_id, record in clients.items():
        if not isinstance(client_id, str) or not CLIENT_ID.fullmatch(client_id):
            raise ContentKouboSlimHandoffError(
                "registry_invalid", "现有 Registry 包含无效 client_id。"
            )
        if not isinstance(record, dict) or set(record) != {"vault_root", "manifest_relative_path"}:
            raise ContentKouboSlimHandoffError(
                "registry_invalid", "现有 Registry 客户记录字段无效。"
            )
        vault_value = record["vault_root"]
        manifest_value = record["manifest_relative_path"]
        if not isinstance(vault_value, str) or not isinstance(manifest_value, str):
            raise ContentKouboSlimHandoffError(
                "registry_invalid", "现有 Registry 路径字段必须是字符串。"
            )
        root = Path(vault_value)
        if not root.is_absolute():
            raise ContentKouboSlimHandoffError(
                "registry_invalid", "现有 Registry 包含非绝对知识库路径。"
            )
        relative = PurePosixPath(manifest_value)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ContentKouboSlimHandoffError(
                "registry_invalid", "现有 Registry 包含不安全 Manifest 路径。"
            )


def _expected_manifest(client_id: str, speaker_mode: str) -> dict[str, Any]:
    return {
        "contract_version": CONTENT_CONTRACT_VERSION,
        "client_id": client_id,
        "asset_roots": dict(CONTENT_ASSET_ROOTS),
        "default_speaker_mode": speaker_mode,
        "allowed_speaker_modes": list(SUPPORTED_SPEAKER_MODES),
        "profile_policy": {
            "required_when": ["personal_ip"],
            "selector": {"status": "active", "is_primary": True},
        },
        "default_platform": "short_video",
        "output_template": CONTENT_OUTPUT_TEMPLATE,
    }


def _registry_plan(
    path: Path,
    *,
    client_id: str,
    vault_root: Path,
    manifest_relative_path: str,
) -> tuple[str, dict[str, Any], str | None]:
    record = {
        "vault_root": str(vault_root),
        "manifest_relative_path": manifest_relative_path,
    }
    if _lstat(path) is None:
        return (
            "create",
            {
                "registry_version": CONTENT_CONTRACT_VERSION,
                "clients": {client_id: record},
            },
            None,
        )
    current, raw = _read_json(path, "Registry")
    _validate_registry(current)
    clients = current["clients"]
    existing = clients.get(client_id)
    if existing is not None and existing != record:
        raise ContentKouboSlimHandoffError(
            "binding_conflict",
            "相同 client_id 已绑定另一知识库，未覆盖。",
        )
    for other_id, other in clients.items():
        if other_id != client_id and other.get("vault_root") == str(vault_root):
            raise ContentKouboSlimHandoffError(
                "binding_conflict",
                "同一知识库已经使用另一 client_id 登记，未重复绑定。",
            )
    if existing == record:
        return "reuse", current, _sha256_bytes(raw)
    merged = json.loads(json.dumps(current, ensure_ascii=False))
    merged["clients"][client_id] = record
    return "merge", merged, _sha256_bytes(raw)


def _manifest_action(path: Path, expected: dict[str, Any]) -> str:
    if _lstat(path) is None:
        return "create"
    current, _ = _read_json(path, "Manifest")
    if current != expected:
        raise ContentKouboSlimHandoffError(
            "binding_conflict",
            f"Manifest 已存在但与本次交接不一致，未覆盖：{path}",
        )
    return "reuse"


def _safe_markdown_candidates(root: Path, label: str) -> list[Path]:
    candidates: list[Path] = []
    try:
        for candidate in sorted(root.rglob("*.md")):
            relative = candidate.relative_to(root)
            current = root
            for part in relative.parts:
                current /= part
                info = _lstat(current)
                if info is None:
                    raise ContentKouboSlimHandoffError(
                        "asset_unreadable", f"{label}文件在检查时消失：{current}"
                    )
                if stat.S_ISLNK(info.st_mode):
                    raise ContentKouboSlimHandoffError(
                        "symlink_rejected", f"{label}包含软链接：{current}"
                    )
            if not stat.S_ISREG(os.lstat(candidate).st_mode):
                raise ContentKouboSlimHandoffError(
                    "asset_invalid", f"{label}包含非普通 Markdown：{candidate}"
                )
            candidates.append(candidate)
    except OSError as exc:
        raise ContentKouboSlimHandoffError(
            "asset_unreadable", f"{label}无法安全检查：{root}"
        ) from exc
    return candidates


def _scalar(value: str) -> Any:
    stripped = value.strip()
    if not stripped:
        return None
    if stripped in {"true", "false", "null"}:
        return {"true": True, "false": False, "null": None}[stripped]
    if stripped.startswith("'") and stripped.endswith("'"):
        return stripped[1:-1]
    if stripped[0] in {'"', "[", "{"}:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return stripped


def _frontmatter(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8", newline=None) as handle:
            first = handle.readline()
            if first.rstrip("\n") != "---":
                raise ContentKouboSlimHandoffError(
                    "frontmatter_missing", f"{label}缺少 frontmatter：{path}"
                )
            lines: list[str] = []
            total = len(first.encode("utf-8"))
            for line in handle:
                total += len(line.encode("utf-8"))
                if total > MAX_FRONTMATTER_BYTES:
                    raise ContentKouboSlimHandoffError(
                        "frontmatter_invalid", f"{label} frontmatter 过大：{path}"
                    )
                normalized = line.rstrip("\n")
                if normalized == "---":
                    break
                lines.append(normalized)
            else:
                raise ContentKouboSlimHandoffError(
                    "frontmatter_invalid", f"{label} frontmatter 未闭合：{path}"
                )
    except ContentKouboSlimHandoffError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ContentKouboSlimHandoffError(
            "frontmatter_invalid", f"{label} frontmatter 无法读取：{path}"
        ) from exc

    metadata: dict[str, Any] = {}
    active_list: str | None = None
    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        list_match = re.fullmatch(r"\s*-\s+(.+?)\s*", raw_line)
        if list_match and active_list is not None:
            metadata[active_list].append(_scalar(list_match.group(1)))
            continue
        key_match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*", raw_line)
        if not key_match:
            active_list = None
            continue
        key, raw_value = key_match.groups()
        if key in metadata:
            raise ContentKouboSlimHandoffError(
                "frontmatter_invalid", f"{label}包含重复字段 {key}：{path}"
            )
        parsed = _scalar(raw_value)
        if parsed is None:
            metadata[key] = []
            active_list = key
        else:
            metadata[key] = parsed
            active_list = None
    return metadata


def _asset_counts(method_root: Path, profile_root: Path) -> tuple[int, int, int]:
    compatible = 0
    skipped = 0
    for candidate in _safe_markdown_candidates(method_root, "04 方法卡"):
        try:
            metadata = _frontmatter(candidate, "04 方法卡")
        except ContentKouboSlimHandoffError as exc:
            if exc.code in {"frontmatter_missing", "frontmatter_invalid"}:
                skipped += 1
                continue
            raise
        if (
            isinstance(metadata.get("asset_id"), str)
            and metadata.get("type") in {
                "benchmark_deconstruction",
                "peer_content_asset",
                "oral_structure",
                "oral_method_asset",
                "content_method_asset",
            }
            and metadata.get("status") == "active"
            and metadata.get("audience_scope") in {
                "consumer",
                "internal_sales_training",
                "both",
            }
            and isinstance(metadata.get("keywords"), list)
            and bool(metadata["keywords"])
            and all(
                isinstance(item, str) and bool(item.strip())
                for item in metadata["keywords"]
            )
            and isinstance(metadata.get("use_when"), list)
            and bool(metadata["use_when"])
            and all(
                isinstance(item, str) and bool(item.strip())
                for item in metadata["use_when"]
            )
            and (
                metadata.get("type") != "content_method_asset"
                or isinstance(metadata.get("applicable_workflows"), list)
                and "content-koubo-slim" in metadata["applicable_workflows"]
            )
        ):
            compatible += 1
        else:
            skipped += 1

    primary = 0
    for candidate in _safe_markdown_candidates(profile_root, "05 Profile"):
        try:
            metadata = _frontmatter(candidate, "05 Profile")
        except ContentKouboSlimHandoffError as exc:
            if exc.code in {"frontmatter_missing", "frontmatter_invalid"}:
                continue
            raise
        if metadata.get("status") == "active" and metadata.get("is_primary") is True:
            primary += 1
    return compatible, skipped, primary


def _preview_sha256(payload: dict[str, Any]) -> str:
    compact = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return _sha256_bytes(compact)


def plan_content_koubo_slim_handoff(
    *,
    binding: Binding,
    registry_path: str | Path | None = None,
    runs_root: str | Path | None = None,
    speaker_mode: str = "neutral",
    manifest_relative_path: str = CONTENT_MANIFEST_RELATIVE_PATH,
) -> ContentKouboSlimHandoffPlan:
    if binding.status != "active" or binding.backend_type != "obsidian":
        raise ContentKouboSlimHandoffError(
            "backend_unsupported",
            "Content 口播 Slim 当前只能接收一个 active 的 Obsidian 本地知识库。",
        )
    if not CLIENT_ID.fullmatch(binding.client_id):
        raise ContentKouboSlimHandoffError(
            "client_id_invalid", "ZSK client_id 不符合 Content 口播 Slim 合同。"
        )
    if speaker_mode not in SUPPORTED_SPEAKER_MODES:
        raise ContentKouboSlimHandoffError(
            "speaker_mode_invalid", "讲述者模式不受 Content 口播 Slim 支持。"
        )

    vault = _existing_directory(binding.backend_locator, "知识库")
    registry = _file_target(
        registry_path or default_content_registry_path(), "Registry"
    )
    runs = _absolute_path(runs_root or default_content_runs_root(), "Runs 目录")
    runs_info = _lstat(runs)
    if runs_info is not None and not stat.S_ISDIR(runs_info.st_mode):
        raise ContentKouboSlimHandoffError(
            "path_conflict", f"Runs 路径已被非目录对象占用：{runs}"
        )
    if _is_below(registry, vault) or _is_below(runs, vault):
        raise ContentKouboSlimHandoffError(
            "binding_conflict", "Registry 和 Runs 必须位于知识库之外。"
        )

    relative_manifest = _safe_relative_json(manifest_relative_path)
    manifest = vault.joinpath(*PurePosixPath(relative_manifest).parts)
    _check_existing_chain(manifest, "Manifest")
    _existing_directory(manifest.parent, "06 配置目录")
    roots = {
        role: _existing_directory(vault / relative, f"{role} 授权目录")
        for role, relative in CONTENT_ASSET_ROOTS.items()
    }
    compatible, skipped, primary = _asset_counts(
        roots["method"], roots["profile"]
    )
    if speaker_mode == "personal_ip" and primary != 1:
        raise ContentKouboSlimHandoffError(
            "profile_contract_invalid",
            "personal_ip 模式必须且只能有一份 active primary Profile。",
        )

    expected_manifest = _expected_manifest(binding.client_id, speaker_mode)
    manifest_action = _manifest_action(manifest, expected_manifest)
    registry_action, expected_registry, registry_before = _registry_plan(
        registry,
        client_id=binding.client_id,
        vault_root=vault,
        manifest_relative_path=relative_manifest,
    )
    preview_payload = {
        "client_id": binding.client_id,
        "vault_root": str(vault),
        "registry_path": str(registry),
        "runs_root": str(runs),
        "manifest_relative_path": relative_manifest,
        "speaker_mode": speaker_mode,
        "registry_action": registry_action,
        "manifest_action": manifest_action,
        "runs_action": "reuse" if runs_info is not None else "create",
        "expected_manifest": expected_manifest,
        "expected_registry": expected_registry,
        "registry_before_sha256": registry_before,
    }
    return ContentKouboSlimHandoffPlan(
        client_id=binding.client_id,
        vault_root=vault,
        registry_path=registry,
        runs_root=runs,
        manifest_path=manifest,
        manifest_relative_path=relative_manifest,
        speaker_mode=speaker_mode,
        registry_action=registry_action,
        manifest_action=manifest_action,
        runs_action="reuse" if runs_info is not None else "create",
        compatible_method_count=compatible,
        skipped_method_count=skipped,
        primary_profile_count=primary,
        expected_manifest=expected_manifest,
        expected_registry=expected_registry,
        registry_before_sha256=registry_before,
        preview_sha256=_preview_sha256(preview_payload),
    )


def _ensure_directory(path: Path, created: list[Path]) -> None:
    info = _lstat(path)
    if info is not None:
        if not stat.S_ISDIR(info.st_mode):
            raise ContentKouboSlimHandoffError(
                "path_conflict", f"目标不是目录：{path}"
            )
        return
    missing: list[Path] = []
    current = path
    while _lstat(current) is None:
        missing.append(current)
        if current.parent == current:
            raise ContentKouboSlimHandoffError(
                "unsafe_path", f"无法确定安全父目录：{path}"
            )
        current = current.parent
    current_info = _lstat(current)
    if current_info is None or not stat.S_ISDIR(current_info.st_mode):
        raise ContentKouboSlimHandoffError(
            "path_conflict", f"父路径不是目录：{current}"
        )
    for directory in reversed(missing):
        try:
            os.mkdir(directory, 0o700)
        except OSError as exc:
            raise ContentKouboSlimHandoffError(
                "write_failed", f"无法创建目录：{directory}"
            ) from exc
        created.append(directory)


def _write_exclusive(path: Path, payload: bytes, created: list[Path]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        created.append(path)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if _read_regular_bytes(path, "写入文件") != payload:
            raise ContentKouboSlimHandoffError(
                "readback_failed", f"写后回读不一致：{path}"
            )
    except ContentKouboSlimHandoffError:
        raise
    except OSError as exc:
        raise ContentKouboSlimHandoffError(
            "write_failed", f"无法 create-only 写入：{path}"
        ) from exc


def _replace_registry(
    plan: ContentKouboSlimHandoffPlan,
    *,
    previous_raw: bytes,
) -> bytes:
    current = _read_regular_bytes(plan.registry_path, "Registry")
    if _sha256_bytes(current) != plan.registry_before_sha256:
        raise ContentKouboSlimHandoffError(
            "binding_conflict", "Registry 在确认后发生变化，未合并。"
        )
    payload = _json_bytes(plan.expected_registry)
    temporary = plan.registry_path.with_name(
        f".{plan.registry_path.name}.{plan.preview_sha256[:16]}.tmp"
    )
    created: list[Path] = []
    try:
        _write_exclusive(temporary, payload, created)
        if _sha256_bytes(_read_regular_bytes(plan.registry_path, "Registry")) != plan.registry_before_sha256:
            raise ContentKouboSlimHandoffError(
                "binding_conflict", "Registry 在写入前发生变化，未合并。"
            )
        os.replace(temporary, plan.registry_path)
        created.clear()
        if _read_regular_bytes(plan.registry_path, "Registry") != payload:
            raise ContentKouboSlimHandoffError(
                "readback_failed", "Registry 合并后回读不一致。"
            )
        return payload
    except Exception:
        for path in created:
            try:
                os.unlink(path)
            except OSError:
                pass
        if _lstat(plan.registry_path) is not None:
            current_after = _read_regular_bytes(plan.registry_path, "Registry")
            if current_after == payload:
                rollback = plan.registry_path.with_name(
                    f".{plan.registry_path.name}.{plan.preview_sha256[:16]}.rollback"
                )
                rollback_created: list[Path] = []
                try:
                    _write_exclusive(rollback, previous_raw, rollback_created)
                    os.replace(rollback, plan.registry_path)
                    rollback_created.clear()
                finally:
                    for path in rollback_created:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
        raise


def _rollback_created(files: list[Path], directories: list[Path]) -> None:
    for path in reversed(files):
        try:
            info = _lstat(path)
            if info is not None and stat.S_ISREG(info.st_mode):
                os.unlink(path)
        except (OSError, ContentKouboSlimHandoffError):
            pass
    for path in reversed(directories):
        try:
            os.rmdir(path)
        except OSError:
            pass


def _response(
    plan: ContentKouboSlimHandoffPlan,
    *,
    status: str,
    reused: bool,
) -> dict[str, Any]:
    waiting = status == "waiting"
    return {
        "status": status,
        "status_label": "等待确认内容资料库" if waiting else "内容资料库连接已完成",
        "message": (
            "已完成零写入预检，请确认完整路径和讲述者模式。"
            if waiting
            else "已有配置已安全复用。"
            if reused
            else "Content 口播 Slim 配置已写入并回读。"
        ),
        "next_action": (
            "确认本次预览，或取消连接。"
            if waiting
            else "安装或重新打开 Content 口播 Slim 后即可开始口播任务。"
        ),
        "confirmation": plan.preview_sha256 if waiting else None,
        "binding": {
            "client_id": plan.client_id,
            "vault_root": str(plan.vault_root),
            "registry_path": str(plan.registry_path),
            "runs_root": str(plan.runs_root),
            "manifest_relative_path": plan.manifest_relative_path,
            "speaker_mode": plan.speaker_mode,
            "registry_action": plan.registry_action,
            "manifest_action": plan.manifest_action,
            "runs_action": plan.runs_action,
            "compatible_method_count": plan.compatible_method_count,
            "skipped_method_count": plan.skipped_method_count,
            "primary_profile_count": plan.primary_profile_count,
        },
    }


def configure_content_koubo_slim_handoff(
    *,
    binding: Binding,
    registry_path: str | Path | None = None,
    runs_root: str | Path | None = None,
    speaker_mode: str = "neutral",
    manifest_relative_path: str = CONTENT_MANIFEST_RELATIVE_PATH,
    confirmation: str | None = None,
) -> dict[str, Any]:
    plan = plan_content_koubo_slim_handoff(
        binding=binding,
        registry_path=registry_path,
        runs_root=runs_root,
        speaker_mode=speaker_mode,
        manifest_relative_path=manifest_relative_path,
    )
    if not plan.needs_write:
        return _response(plan, status="completed", reused=True)
    if confirmation is None:
        return _response(plan, status="waiting", reused=False)
    if confirmation != plan.preview_sha256:
        raise ContentKouboSlimHandoffError(
            "confirmation_mismatch", "确认信息与当前预览不一致，零写入。"
        )

    created_files: list[Path] = []
    created_directories: list[Path] = []
    registry_previous: bytes | None = None
    registry_written: bytes | None = None
    try:
        _ensure_directory(plan.registry_path.parent, created_directories)
        _ensure_directory(plan.runs_root, created_directories)
        if plan.manifest_action == "create":
            _write_exclusive(
                plan.manifest_path,
                _json_bytes(plan.expected_manifest),
                created_files,
            )
        if plan.registry_action == "create":
            _write_exclusive(
                plan.registry_path,
                _json_bytes(plan.expected_registry),
                created_files,
            )
        elif plan.registry_action == "merge":
            registry_previous = _read_regular_bytes(plan.registry_path, "Registry")
            registry_written = _replace_registry(
                plan, previous_raw=registry_previous
            )

        manifest_value, _ = _read_json(plan.manifest_path, "Manifest")
        registry_value, _ = _read_json(plan.registry_path, "Registry")
        if manifest_value != plan.expected_manifest or registry_value != plan.expected_registry:
            raise ContentKouboSlimHandoffError(
                "readback_failed", "配置写后回读与确认预览不一致。"
            )
        if not plan.runs_root.is_dir() or plan.runs_root.is_symlink():
            raise ContentKouboSlimHandoffError(
                "readback_failed", "Runs 目录写后回读失败。"
            )
    except Exception:
        if registry_previous is not None and registry_written is not None:
            try:
                if _read_regular_bytes(plan.registry_path, "Registry") == registry_written:
                    rollback = plan.registry_path.with_name(
                        f".{plan.registry_path.name}.{plan.preview_sha256[:16]}.restore"
                    )
                    rollback_created: list[Path] = []
                    _write_exclusive(rollback, registry_previous, rollback_created)
                    os.replace(rollback, plan.registry_path)
            except Exception:
                pass
        _rollback_created(created_files, created_directories)
        raise
    return _response(plan, status="completed", reused=False)
