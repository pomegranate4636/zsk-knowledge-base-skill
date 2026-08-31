"""客户可见的来源命名与 Obsidian 来源定位。"""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path, PurePath
import re
import stat


_UNSAFE = re.compile(r"[\\/\x00-\x1f:<>\"|?*]")


def human_source_label(title: str, *, day: date | None = None) -> str:
    """生成只含日期和人类标题的展示名；机器指纹留在正文元数据中。"""
    clean = safe_title(title)
    return f"{(day or date.today()).isoformat()} {clean}"


def safe_title(value: str) -> str:
    clean = _UNSAFE.sub("-", value.strip()).strip(". -")
    clean = re.sub(r"\s+", " ", clean)
    return clean[:80] or "未命名资料"


def source_original_name(display_name: str, original_name: str) -> str:
    suffix = PurePath(original_name).suffix.lower()
    return f"{safe_title(display_name)}-原件{suffix}"


def source_readable_name(display_name: str) -> str:
    return f"{safe_title(display_name)}-可读版.md"


def page_file_name(page_number: int) -> str:
    return f"第{page_number:03d}页.png"


def find_obsidian_source_dir(root: Path, source_id: str) -> Path | None:
    """兼容旧 SRC 目录，并从人类命名目录的可读版元数据找回稳定来源。"""
    source_root = root / "01-来源索引"
    legacy = source_root / source_id
    if _normal_directory(legacy):
        return legacy
    try:
        entries = tuple(source_root.iterdir())
    except OSError:
        return None
    marker = f'source_id: "{source_id}"'
    matches: list[Path] = []
    for entry in entries:
        if not _normal_directory(entry):
            continue
        try:
            readable_files = tuple(path for path in entry.glob("*-可读版.md") if _normal_file(path))
            if len(readable_files) == 1 and marker in readable_files[0].read_text(encoding="utf-8"):
                matches.append(entry)
        except (OSError, UnicodeError):
            continue
    return matches[0] if len(matches) == 1 else None


def unique_source_dir(source_root: Path, display_name: str) -> Path:
    base = safe_title(display_name)
    candidate = source_root / base
    number = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = source_root / f"{base}-{number}"
        number += 1
    return candidate


def _normal_directory(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISDIR(mode) and not stat.S_ISLNK(mode)


def _normal_file(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)
