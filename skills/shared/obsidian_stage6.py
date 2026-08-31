"""03/04/05 共用的 Obsidian 资产存储器。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from .contracts import AdapterResult, AssetPayload, BackendObjectRef
from .naming import find_obsidian_source_dir


_UNSAFE = re.compile(r"[\\/\x00-\x1f:]")
_ORIGINAL_NAMES = frozenset({"original.bin", "original.md", "original.txt", "original.csv", "original.json", "original.html", "original.htm", "original.docx", "original.pptx", "original.xlsx", "original.pdf"})


class ObsidianStage6Storage:
    """保留旧名称，避免阶段 6 调用方分叉；目标由调用方明确传入。"""

    def __init__(self, root: Path, *, asset_root: str = "03", source_role: str = "business_knowledge", kind: str = "knowledge_asset", group_by_topic: bool = True) -> None:
        self.root = root
        self.asset_root = asset_root
        self.source_role = source_role
        self.kind = kind
        self.group_by_topic = group_by_topic

    def write(self, asset: AssetPayload) -> AdapterResult:
        source = find_obsidian_source_dir(self.root, asset.source_id)
        if source is None:
            return AdapterResult.failed("ownership_unknown", "Asset source is not fully registered in 01.", blocked=True)
        try:
            originals = tuple(path for path in source.iterdir() if path.name in _ORIGINAL_NAMES or path.name.endswith("-原件" + path.suffix))
            readable_candidates = tuple(path for path in source.iterdir() if path.name == "readable.md" or path.name.endswith("-可读版.md"))
            if not originals:
                return AdapterResult.failed("ownership_unknown", "Asset source is not fully registered in 01.", blocked=True)
            if len(originals) != 1:
                return AdapterResult.failed("duplicate_conflict", "Asset source has more than one registered original.", blocked=True)
            if len(readable_candidates) != 1:
                return AdapterResult.failed("ownership_unknown", "Asset source has no unique readable copy.", blocked=True)
            original = originals[0]
            readable = readable_candidates[0]
            if any(not path.is_file() or path.is_symlink() for path in (original, readable)):
                return AdapterResult.failed("ownership_unknown", "Asset source is not fully registered in 01.", blocked=True)
            content = readable.read_text(encoding="utf-8")
            if asset.source_id not in content:
                return AdapterResult.failed("ownership_unknown", "Registered source identity cannot be verified.", blocked=True)
        except OSError:
            return AdapterResult.failed("readback_failed", "Knowledge card source cannot be read safely.", blocked=True)
        title = self._part(asset.title)
        directory = self.root / {"03": "03-业务知识库", "04": "04-内容方法库", "05": "05-IP-Profile"}[self.asset_root]
        if self.group_by_topic:
            directory /= self._part(str(asset.metadata.get("topic") or "未分类"))
        try:
            if directory.exists():
                mode = os.lstat(directory).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    return AdapterResult.failed("structure_conflict", "Knowledge topic path is not a normal directory.", blocked=True)
            else:
                os.mkdir(directory, 0o700)
            path = directory / f"{title}.md"
            payload = asset.body.encode("utf-8")
            if path.exists() or path.is_symlink():
                mode = os.lstat(path).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    return AdapterResult.failed("structure_conflict", "Knowledge card path is not a normal file.", blocked=True)
                if path.read_bytes() != payload:
                    return AdapterResult.failed("duplicate_conflict", "Knowledge card cannot overwrite different content.", blocked=True)
                return AdapterResult.reused(self._ref(asset), checked=("create_only", "content_readback", "source_id"))
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
            if path.read_bytes() != payload:
                return AdapterResult.failed("readback_failed", "Knowledge card content failed readback.", blocked=True)
        except OSError:
            return AdapterResult.failed("write_failed", "Knowledge asset cannot be written safely.")
        return AdapterResult.ok(self._ref(asset), checked=("create_only", "content_readback", "source_id"))

    @staticmethod
    def _part(value: str) -> str:
        clean = _UNSAFE.sub("-", value.strip()).strip(". -")
        return clean[:72] or "未命名"

    def _ref(self, asset: AssetPayload) -> BackendObjectRef:
        opaque = hashlib.sha256(asset.asset_id.encode("utf-8")).hexdigest()[:16]
        return BackendObjectRef(f"obsidian-{self.kind}-{opaque}", self.kind, f"obsidian://{self.asset_root}/{opaque}", asset.fingerprint()[:16])
