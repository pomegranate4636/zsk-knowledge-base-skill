"""阶段 4 的最小 Obsidian 建库 Adapter。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePath
import stat
from typing import Sequence

from .contracts import AdapterResult, AssetPayload, BackendObjectRef, Binding, ContractError, ExceptionRecord, PageArtifact, PageTextEvidence, SourceRecord, ROOT_KEYS
from .content_source_contract import (
    ContentSourceContractError,
    preflight_obsidian_profile_index,
    sync_obsidian_profile_index,
)
from .naming import find_obsidian_source_dir, page_file_name, source_original_name, source_readable_name, unique_source_dir
from .obsidian_stage6 import ObsidianStage6Storage
from .templates import ROOT_TITLES, root_content, root_object_kind, template_fingerprint


_RULE_KEYS = frozenset({"AGENTS", "README"})
_SAFE_ORIGINAL_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json", ".html", ".htm", ".docx", ".pptx", ".xlsx", ".pdf"})
_ROOT_NAMES = {key: f"{ROOT_TITLES[key]}.md" if key in _RULE_KEYS else ROOT_TITLES[key] for key in ROOT_KEYS}
_OBSIDIAN_CONTROL_DIRECTORIES = frozenset({".obsidian"})


def _rule_body(content: str) -> str:
    return content.partition("\n")[2]


def _rule_content_matches(binding: Binding, key: str, content: str) -> bool:
    # AGENTS/README 是客户可编辑说明；建库后只要求可读且非空，绝不静默覆盖。
    return bool(content.strip())


def canonical_obsidian_locator(locator: str) -> str | None:
    """接受已有普通目录；不解析 HOME、相对路径或任何软链接。"""
    if not isinstance(locator, str) or not locator.strip():
        return None
    raw = locator.strip()
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        return None
    try:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return None
        return str(path) if stat.S_ISDIR(os.lstat(path).st_mode) else None
    except (OSError, ValueError):
        return None


class ObsidianAdapter:
    """实现根骨架与阶段 5 的 01/02 本地存储。"""

    def __init__(self) -> None:
        self._binding_key: str | None = None
        self._root: Path | None = None
        self._source_refs: dict[str, BackendObjectRef] = {}
        self._source_paths: dict[str, Path] = {}
        self._source_dirs: dict[str, Path] = {}
        self._asset_refs: dict[str, BackendObjectRef] = {}

    def doctor(self) -> AdapterResult:
        return AdapterResult.ok(checked=("local_filesystem", "explicit_existing_directory_required"))

    def resolve_binding(self, binding: Binding) -> AdapterResult:
        if binding.backend_type != "obsidian":
            return AdapterResult.failed("backend_unsupported", "Obsidian Adapter only accepts the obsidian backend.", blocked=True)
        locator = canonical_obsidian_locator(binding.backend_locator)
        if locator is None:
            return AdapterResult.failed("binding_missing", "Obsidian target must be an explicit existing safe directory.", blocked=True)
        key = self._binding_digest(binding)
        if self._binding_key is not None and self._binding_key != key:
            return AdapterResult.failed("binding_conflict", "Adapter is already resolved for another binding.", blocked=True)
        if self._root is not None:
            return AdapterResult.reused(checked=("client_id", "canonical_target", "safe_directory"), metadata={"locator": "obsidian://root"})
        self._binding_key = key
        self._root = Path(locator)
        return AdapterResult.ok(checked=("client_id", "canonical_target", "safe_directory"), metadata={"locator": "obsidian://root"})

    def inspect_structure(self, binding: Binding) -> AdapterResult:
        guard = self._binding_guard(binding)
        if guard:
            return guard
        found, missing, failure = self._scan(binding)
        if failure:
            return failure
        refs = tuple(self._ref(binding, key) for key in found)
        if not found:
            return AdapterResult.ok(checked=("nine_root_objects_absent",), metadata={"structure_state": "empty", "existing_root_keys": [], "missing_root_keys": list(missing)})
        if missing:
            return AdapterResult.ok(*refs, checked=("partial_root_objects",), metadata={"structure_state": "partial", "existing_root_keys": list(found), "missing_root_keys": list(missing)})
        return AdapterResult.reused(*refs, checked=("nine_root_objects_present", "rules_match_template"), metadata={"structure_state": "complete", "existing_root_keys": list(found), "missing_root_keys": []})

    def create_skeleton(self, binding: Binding) -> AdapterResult:
        guard = self._binding_guard(binding)
        if guard:
            return guard
        found, missing, failure = self._scan(binding)
        if failure:
            return failure
        if not missing:
            return AdapterResult.reused(*tuple(self._ref(binding, key) for key in found), checked=("create_only", "nine_root_objects_present"))
        for key in missing:
            failure = self._create_root(binding, key)
            if failure:
                return failure
        final = self.inspect_structure(binding)
        if final.status != "reused":
            return final
        check = "nine_root_objects_created" if len(missing) == len(ROOT_KEYS) else "missing_root_objects_created"
        return AdapterResult.ok(*final.object_refs, checked=("create_only", check, "rules_written", "rules_readback"))

    def read_rules(self, binding: Binding) -> AdapterResult:
        inspected = self.inspect_structure(binding)
        if inspected.status == "blocked":
            return inspected
        if inspected.status != "reused":
            return AdapterResult.failed("structure_conflict", "Root rules require a complete verified skeleton.", blocked=True)
        refs = tuple(ref for ref in inspected.object_refs if ref.object_id in {self._ref(binding, key).object_id for key in _RULE_KEYS})
        return AdapterResult.ok(*refs, checked=("agents_read", "readme_read", "rules_nonempty", "customer_rules_unmodified"))

    def store_original(self, binding: Binding, source: SourceRecord, payload: bytes) -> AdapterResult:
        return self._store_source(binding, source, payload, "original")

    def store_readable(self, binding: Binding, source: SourceRecord, payload: bytes) -> AdapterResult:
        return self._store_source(binding, source, payload, "readable")

    def reuse_existing_source(
        self,
        binding: Binding,
        source_id: str,
        original_sha256: str,
        requested_role: str,
        requested_page_evidence_mode: str,
        current_original_retention_approved: bool,
    ) -> AdapterResult | None:
        """回读已登记的同 SHA 来源，兼容旧 SRC 目录和新版中文目录。"""
        guard = self._write_guard(binding)
        if guard:
            return guard
        assert self._root is not None
        source_dir = find_obsidian_source_dir(self._root, source_id)
        if source_dir is None:
            return None
        try:
            record, refs = self._existing_source_record(
                binding,
                source_dir,
                source_id,
                original_sha256,
                requested_role,
                requested_page_evidence_mode,
                current_original_retention_approved,
            )
        except ContractError as exc:
            return AdapterResult.failed(exc.code, exc.detail, blocked=True)
        except ValueError as exc:
            return AdapterResult.failed("readback_failed", str(exc), blocked=True)
        for ref, path in refs:
            self._source_refs[ref.object_id] = ref
            self._source_paths[ref.object_id] = path
        self._source_dirs[source_id] = source_dir
        return AdapterResult.reused(
            *(ref for ref, _path in refs),
            checked=("existing_source_identity", "existing_source_hash", "existing_source_layout"),
            metadata={"source_record": record, "layout": "legacy_src" if source_dir.name == source_id else "human_named"},
        )

    def store_page_evidence(self, binding: Binding, source: SourceRecord, page: PageArtifact, payload: bytes) -> AdapterResult:
        guard = self._write_guard(binding)
        if guard:
            return guard
        if source.client_id != binding.client_id or page.source_id != source.source_id or page not in source.page_artifacts:
            return AdapterResult.failed("binding_conflict", "Page evidence does not belong to the active source.", blocked=True)
        if hashlib.sha256(payload).hexdigest() != page.sha256:
            return AdapterResult.failed("source_unreadable", "Page payload hash does not match its manifest.", blocked=True)
        assert self._root is not None
        source_dir = self._source_dirs.get(source.source_id) or find_obsidian_source_dir(self._root, source.source_id)
        if source_dir is None:
            return AdapterResult.failed("ownership_unknown", "Source directory is unavailable for page evidence.", blocked=True)
        page_dir = source_dir / "页面证据"
        try:
            if page_dir.exists():
                mode = os.lstat(page_dir).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    return AdapterResult.failed("structure_conflict", "Page evidence directory type is invalid.", blocked=True)
            else:
                os.mkdir(page_dir, 0o700)
        except OSError:
            return AdapterResult.failed("write_failed", "Page evidence directory cannot be created safely.")
        name = page_file_name(page.page_number)
        path = page_dir / name
        result = self._store_bytes(
            path,
            payload,
            page.page_id,
            "source_page",
            f"obsidian://01/{source_dir.name}/页面证据",
        )
        if result.status in {"ok", "reused"}:
            for ref in result.object_refs:
                self._source_refs[ref.object_id] = ref
                self._source_paths[ref.object_id] = path
        return result

    def write_exception(self, binding: Binding, exception: ExceptionRecord) -> AdapterResult:
        guard = self._write_guard(binding)
        if guard:
            return guard
        assert self._root is not None
        payload = (f"# 待审核 {exception.exception_id}\n\n原因码：`{exception.reason_code}`\n\n{exception.safe_note}\n\n待确认：{exception.question}\n").encode("utf-8")
        return self._store_bytes(self._root / _ROOT_NAMES["02"] / f"{exception.exception_id}.md", payload, exception.exception_id, "exception", "obsidian://02")

    def write_knowledge_asset(self, binding: Binding, asset: AssetPayload) -> AdapterResult:
        if not isinstance(asset, AssetPayload):
            return self._later_stage("write_knowledge_asset")
        guard = self._write_guard(binding)
        if guard:
            return guard
        assert self._root is not None
        return self._write_asset(asset, "03", "business_knowledge", "knowledge_asset", group_by_topic=True)

    def write_method_asset(self, binding: Binding, asset: AssetPayload) -> AdapterResult:
        if not isinstance(asset, AssetPayload):
            return self._later_stage("write_method_asset")
        guard = self._write_guard(binding)
        if guard:
            return guard
        return self._write_asset(asset, "04", "reference_method", "method_asset", group_by_topic=True)

    def write_profile(self, binding: Binding, asset: AssetPayload) -> AdapterResult:
        if not isinstance(asset, AssetPayload):
            return self._later_stage("write_profile")
        guard = self._write_guard(binding)
        if guard:
            return guard
        assert self._root is not None
        try:
            preflight_obsidian_profile_index(self._root, asset)
        except (OSError, ValueError, ContentSourceContractError) as exc:
            return AdapterResult.failed("version_conflict", str(exc), blocked=True)
        result = self._write_asset(asset, "05", "profile_material", "profile", group_by_topic=False)
        if result.status not in {"ok", "reused"}:
            return result
        try:
            sync_obsidian_profile_index(self._root, asset)
        except (OSError, ValueError, ContentSourceContractError) as exc:
            return AdapterResult.failed("readback_failed", f"Profile 已写入，但索引未通过回读：{exc}", blocked=True)
        return result

    def read_back(self, binding: Binding, refs: Sequence[BackendObjectRef] | None = None) -> AdapterResult:
        inspected = self.inspect_structure(binding)
        if inspected.status == "blocked":
            return inspected
        if inspected.status != "reused":
            return AdapterResult.failed("readback_failed", "Complete root structure is unavailable for readback.", blocked=True)
        actual = {ref.object_id: ref for ref in inspected.object_refs} | self._source_refs | self._asset_refs
        requested = tuple(refs or inspected.object_refs)
        if any(actual.get(ref.object_id) != ref for ref in requested):
            return AdapterResult.failed("readback_failed", "Stored reference changed after inspection.", blocked=True)
        for ref in requested:
            path = self._source_paths.get(ref.object_id)
            if path is None:
                continue
            try:
                mode = os.lstat(path).st_mode
                digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            except OSError:
                return AdapterResult.failed("readback_failed", "Stored source cannot be read back.", blocked=True)
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or digest != ref.version:
                return AdapterResult.failed("readback_failed", "Stored source content changed after write.", blocked=True)
        if any(ref.object_id in self._source_refs for ref in requested):
            checks = ("source_write_readback", "payload_sha256", "stable_relative_refs")
        elif any(ref.object_id in self._asset_refs for ref in requested):
            checks = ("asset_write_readback", "stable_relative_refs")
        else:
            checks = ("root_types", "rules_nonempty", "rules_sha256", "stable_relative_refs")
        return AdapterResult.ok(*requested, checked=checks)

    def _write_asset(self, asset: AssetPayload, asset_root: str, source_role: str, kind: str, *, group_by_topic: bool) -> AdapterResult:
        assert self._root is not None
        result = ObsidianStage6Storage(self._root, asset_root=asset_root, source_role=source_role, kind=kind, group_by_topic=group_by_topic).write(asset)
        if result.status in {"ok", "reused"}:
            ref = result.object_refs[0]
            self._asset_refs[ref.object_id] = ref
        return result

    def _binding_guard(self, binding: Binding) -> AdapterResult | None:
        locator = canonical_obsidian_locator(binding.backend_locator)
        if self._root is None or self._binding_key is None or locator is None:
            return AdapterResult.failed("binding_missing", "Binding has not been resolved to a safe existing directory.", blocked=True)
        if self._binding_key != self._binding_digest(binding) or self._root != Path(locator):
            return AdapterResult.failed("binding_conflict", "Resolved binding differs from requested binding.", blocked=True)
        return None

    def _scan(self, binding: Binding) -> tuple[tuple[str, ...], tuple[str, ...], AdapterResult | None]:
        assert self._root is not None
        try:
            entries = {entry.name: entry for entry in os.scandir(self._root)}
        except OSError:
            return (), (), AdapterResult.failed("readback_failed", "Target directory cannot be read safely.", blocked=True)
        if set(entries) - set(_ROOT_NAMES.values()) - _OBSIDIAN_CONTROL_DIRECTORIES:
            return (), (), AdapterResult.failed("structure_conflict", "Target contains an unknown root object.", blocked=True)
        for name in _OBSIDIAN_CONTROL_DIRECTORIES:
            entry = entries.get(name)
            if entry is None:
                continue
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                return (), (), AdapterResult.failed("readback_failed", "Obsidian control directory cannot be inspected safely.", blocked=True)
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                return (), (), AdapterResult.failed("structure_conflict", "Obsidian control directory type is invalid.", blocked=True)
        found: list[str] = []
        for key in ROOT_KEYS:
            entry = entries.get(_ROOT_NAMES[key])
            if entry is None:
                continue
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                return (), (), AdapterResult.failed("readback_failed", "Root object cannot be inspected safely.", blocked=True)
            expected_directory = root_object_kind(binding, key) == "directory"
            if stat.S_ISLNK(mode) or (expected_directory and not stat.S_ISDIR(mode)) or (not expected_directory and not stat.S_ISREG(mode)):
                return (), (), AdapterResult.failed("structure_conflict", "Root object type is invalid.", blocked=True)
            if key in _RULE_KEYS and not self._rule_matches(binding, key, Path(entry.path)):
                return (), (), AdapterResult.failed("structure_conflict", "Root rules are customer-owned or differ from the template.", blocked=True)
            found.append(key)
        missing = tuple(key for key in ROOT_KEYS if key not in found)
        return tuple(found), missing, None

    def _create_root(self, binding: Binding, key: str) -> AdapterResult | None:
        assert self._root is not None
        path = self._root / _ROOT_NAMES[key]
        try:
            if root_object_kind(binding, key) == "directory":
                os.mkdir(path, 0o700)
            else:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(root_content(binding, key))
        except FileExistsError:
            return AdapterResult.failed("structure_conflict", "Root object appeared during create-only setup.", blocked=True)
        except OSError:
            return AdapterResult.failed("write_failed", "Create-only root setup failed.", blocked=True)
        return None

    def _write_guard(self, binding: Binding) -> AdapterResult | None:
        guard = self._binding_guard(binding)
        if guard:
            return guard
        inspected = self.inspect_structure(binding)
        if inspected.status != "reused":
            return inspected if inspected.status in {"blocked", "failed"} else AdapterResult.failed("structure_conflict", "Stage 5 requires a complete skeleton.", blocked=True)
        return None

    def _store_source(self, binding: Binding, source: SourceRecord, payload: bytes, kind: str) -> AdapterResult:
        guard = self._write_guard(binding)
        if guard:
            return guard
        if source.client_id != binding.client_id:
            return AdapterResult.failed("binding_conflict", "Source belongs to another binding.", blocked=True)
        expected = source.original_sha256 if kind == "original" else source.readable_sha256
        if hashlib.sha256(payload).hexdigest() != expected:
            return AdapterResult.failed("source_unreadable", "Source payload hash does not match its record.", blocked=True)
        assert self._root is not None
        source_root = self._root / _ROOT_NAMES["01"]
        source_dir = self._source_dirs.get(source.source_id) or find_obsidian_source_dir(self._root, source.source_id)
        if source_dir is None:
            source_dir = unique_source_dir(source_root, source.display_name or source.source_title)
        try:
            if source_dir.exists():
                mode = os.lstat(source_dir).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    return AdapterResult.failed("structure_conflict", "Source directory type is invalid.", blocked=True)
            else:
                os.mkdir(source_dir, 0o700)
            self._source_dirs[source.source_id] = source_dir
        except OSError:
            return AdapterResult.failed("write_failed", "Source directory cannot be created safely.")
        legacy_source = source_dir.name == source.source_id
        if kind == "original":
            legacy = source_dir / "original.bin"
            suffix = PurePath(source.original_name).suffix.lower()
            name = (
                "original.bin" if legacy.exists() or legacy.is_symlink()
                else f"original{suffix if suffix in _SAFE_ORIGINAL_SUFFIXES else '.bin'}" if legacy_source
                else source_original_name(source.display_name or source.source_title, source.original_name)
            )
        else:
            name = "readable.md" if legacy_source else source_readable_name(source.display_name or source.source_title)
        path = source_dir / name
        result = self._store_bytes(path, payload, source.source_id, f"source_{kind}", f"obsidian://01/{source_dir.name}")
        if result.status in {"ok", "reused"}:
            for ref in result.object_refs:
                self._source_refs[ref.object_id] = ref
                self._source_paths[ref.object_id] = path
        return result

    def _existing_source_record(
        self,
        binding: Binding,
        source_dir: Path,
        source_id: str,
        original_sha256: str,
        requested_role: str,
        requested_page_evidence_mode: str,
        current_original_retention_approved: bool,
    ) -> tuple[SourceRecord, tuple[tuple[BackendObjectRef, Path], ...]]:
        readable_candidates = tuple(
            path for path in source_dir.iterdir()
            if path.name == "readable.md" or path.name.endswith("-可读版.md")
        )
        if len(readable_candidates) != 1:
            raise ValueError("Registered source has no unique readable copy.")
        readable = readable_candidates[0]
        if not self._normal_file(readable):
            raise ValueError("Registered readable copy is unsafe.")
        meta = self._frontmatter(readable)
        if meta.get("source_id") != source_id or meta.get("original_sha256") != original_sha256:
            raise ValueError("Registered source metadata does not match this original.")
        if meta.get("client_id") != binding.client_id:
            raise ContractError("binding_conflict", "Registered source belongs to another or unknown client binding.")
        original_name = meta.get("original_file_name")
        source_title = meta.get("source_title")
        if not isinstance(original_name, str) or not original_name or not isinstance(source_title, str) or not source_title:
            raise ValueError("Registered source naming metadata is incomplete.")
        suffix = PurePath(original_name).suffix.lower()
        originals = tuple(
            path for path in source_dir.iterdir()
            if path.name.startswith("original.") or path.name == "original.bin" or path.name.endswith(f"-原件{suffix}")
        )
        if len(originals) != 1 or not self._normal_file(originals[0]):
            raise ValueError("Registered source has no unique original copy.")
        original = originals[0]
        if hashlib.sha256(original.read_bytes()).hexdigest() != original_sha256:
            raise ValueError("Registered original hash does not match this source.")
        source_role = meta.get("source_role") if isinstance(meta.get("source_role"), str) else "unknown"
        if source_role not in {"business_knowledge", "reference_method", "profile_material", "mixed", "unknown"}:
            raise ValueError("Registered source role is invalid.")
        if requested_role not in {"unknown", "mixed", source_role} and source_role not in {"unknown", "mixed"}:
            raise ContractError("duplicate_conflict", "Registered source role conflicts with this request.")
        page_mode = meta.get("page_evidence_mode") if isinstance(meta.get("page_evidence_mode"), str) else "off"
        page_count = meta.get("page_count") if isinstance(meta.get("page_count"), int) else 0
        if page_mode == "required" and not current_original_retention_approved:
            raise ContractError("privacy_approval_required", "Registered page evidence requires current original retention approval.")
        if requested_page_evidence_mode == "required" and page_mode != "required":
            raise ContractError("page_evidence_failed", "Registered source has no complete page evidence.")
        page_artifacts: tuple[PageArtifact, ...] = ()
        page_text_evidence: tuple[PageTextEvidence, ...] = ()
        page_refs: list[tuple[BackendObjectRef, Path]] = []
        if page_mode == "required":
            manifest = meta.get("page_manifest")
            if not isinstance(manifest, list) or page_count != len(manifest):
                raise ValueError("Registered page manifest is incomplete.")
            try:
                page_artifacts = tuple(PageArtifact(**item) for item in manifest)
            except (TypeError, ValueError):
                raise ValueError("Registered page manifest is invalid.") from None
            text_manifest = meta.get("page_text_manifest")
            if text_manifest is not None:
                if not isinstance(text_manifest, list) or len(text_manifest) != page_count:
                    raise ValueError("Registered page text manifest is incomplete.")
                try:
                    page_text_evidence = tuple(PageTextEvidence(**item) for item in text_manifest)
                except (TypeError, ValueError):
                    raise ValueError("Registered page text manifest is invalid.") from None
            page_dir = source_dir / "pages"
            if not page_dir.is_dir():
                page_dir = source_dir / "页面证据"
            if not page_dir.is_dir() or page_dir.is_symlink():
                raise ValueError("Registered page evidence directory is unavailable.")
            for page in page_artifacts:
                legacy = tuple(page_dir.glob(f"page-{page.page_number:03d}-*.png"))
                candidates = legacy or (page_dir / page_file_name(page.page_number),)
                if len(candidates) != 1 or not self._normal_file(candidates[0]):
                    raise ValueError("Registered page evidence is incomplete.")
                page_path = candidates[0]
                if hashlib.sha256(page_path.read_bytes()).hexdigest() != page.sha256:
                    raise ValueError("Registered page evidence hash differs.")
                page_refs.append((self._existing_ref(f"source_page-{page.page_id}", "source_page", source_dir, page_path), page_path))
        elif page_mode != "off" or page_count != 0:
            raise ValueError("Registered page evidence mode is invalid.")
        privacy = meta.get("privacy_status") if isinstance(meta.get("privacy_status"), str) else "passed"
        permission = meta.get("permission_status") if isinstance(meta.get("permission_status"), str) else "allowed"
        if privacy not in {"passed", "redacted"} or permission != "allowed":
            raise ValueError("Registered source approval metadata is invalid.")
        version_of = meta.get("version_of") if isinstance(meta.get("version_of"), str) else None
        retained = meta.get("original_retention_approved")
        if not isinstance(retained, bool):
            retained = privacy == "passed"
        display = meta.get("display_name") if isinstance(meta.get("display_name"), str) and meta.get("display_name") else source_title
        content_kind = "presentation" if suffix == ".pptx" else "table" if suffix in {".xlsx", ".csv"} else "document" if suffix in {".pdf", ".docx"} else "text"
        original_ref = self._existing_ref(f"source_original-{source_id}", "source_original", source_dir, original)
        readable_ref = self._existing_ref(f"source_readable-{source_id}", "source_readable", source_dir, readable)
        record = SourceRecord(
            "zsk-source-record-v1", source_id, binding.client_id, source_title, source_role, content_kind,
            original_name, original_sha256, hashlib.sha256(readable.read_bytes()).hexdigest(), privacy, permission,
            version_of, "reused", retained, {"original": original_ref, "readable": readable_ref},
            page_mode, page_count, page_artifacts, display, page_text_evidence,
        )
        return record, ((original_ref, original), (readable_ref, readable), *page_refs)

    @staticmethod
    def _frontmatter(path: Path) -> dict[str, object]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ValueError("Registered readable copy cannot be read.") from exc
        if not lines or lines[0] != "---":
            raise ValueError("Registered readable copy has no frontmatter.")
        fields: dict[str, object] = {}
        for line in lines[1:]:
            if line == "---":
                return fields
            key, separator, value = line.partition(": ")
            if not separator:
                raise ValueError("Registered readable frontmatter is malformed.")
            try:
                fields[key] = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("Registered readable frontmatter is malformed.") from exc
        raise ValueError("Registered readable frontmatter is incomplete.")

    @staticmethod
    def _normal_file(path: Path) -> bool:
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            return False
        return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)

    @staticmethod
    def _existing_ref(object_id: str, object_kind: str, source_dir: Path, path: Path) -> BackendObjectRef:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        relative = path.relative_to(source_dir).as_posix()
        return BackendObjectRef(f"obsidian-{object_id}", object_kind, f"obsidian://01/{source_dir.name}/{relative}", digest)

    @staticmethod
    def _store_bytes(path: Path, payload: bytes, object_key: str, object_kind: str, locator_root: str) -> AdapterResult:
        digest = hashlib.sha256(payload).hexdigest()
        try:
            if path.exists() or path.is_symlink():
                mode = os.lstat(path).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    return AdapterResult.failed("structure_conflict", "Stored object type is invalid.", blocked=True)
                if path.read_bytes() != payload:
                    return AdapterResult.failed("version_conflict", "Create-only object content differs.", blocked=True)
                ref = BackendObjectRef(f"obsidian-{object_kind}-{object_key}", object_kind, f"{locator_root}/{path.name}", digest[:16])
                return AdapterResult.reused(ref, checked=("create_only", "payload_sha256", "readback"))
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return AdapterResult.failed("readback_failed", "Stored object failed hash readback.", blocked=True)
        except OSError:
            return AdapterResult.failed("write_failed", "Create-only object write failed.")
        ref = BackendObjectRef(f"obsidian-{object_kind}-{object_key}", object_kind, f"{locator_root}/{path.name}", digest[:16])
        return AdapterResult.ok(ref, checked=("create_only", "payload_sha256", "readback"))

    @staticmethod
    def _binding_digest(binding: Binding) -> str:
        material = "\n".join((binding.client_id, binding.subject_type, binding.backend_type, binding.backend_locator, binding.template_version, *[f"{key}:{value}" for key, value in sorted(binding.root_map.items())]))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _later_stage(method: str) -> AdapterResult:
        return AdapterResult.failed("format_unsupported", f"{method} is unavailable before its assigned ZSK stage.", blocked=True)

    @staticmethod
    def _rule_matches(binding: Binding, key: str, path: Path) -> bool:
        try:
            return _rule_content_matches(binding, key, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            return False

    @staticmethod
    def _ref(binding: Binding, key: str) -> BackendObjectRef:
        opaque = hashlib.sha256(f"{binding.client_id}:{key}".encode("utf-8")).hexdigest()[:16]
        version = template_fingerprint(_rule_body(root_content(binding, key)))[:16] if key in _RULE_KEYS else "1"
        return BackendObjectRef(f"obsidian-root-{opaque}", root_object_kind(binding, key), f"obsidian://root/{key}", version)
