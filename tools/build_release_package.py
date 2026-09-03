#!/usr/bin/env python3
"""Build the complete ZSK installation ZIP from a clean Git tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "dist" / "ZSK-完整安装包-20260903.zip"
EXACT_MEMBERS = frozenset(("README.md", "install.py", "requirements-markitdown.txt"))
MEMBER_ROOTS = ("schemas/", "skills/")
MANIFEST_NAME = "BUILD-MANIFEST.json"
FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def _is_bytecode(relative: str) -> bool:
    path = PurePosixPath(relative)
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def select_release_members(tracked_files: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    selected: set[str] = set()
    for raw in tracked_files:
        relative = raw.replace("\\", "/")
        if _is_bytecode(relative):
            continue
        if relative in EXACT_MEMBERS or relative.startswith(MEMBER_ROOTS):
            selected.add(relative)
    return tuple(sorted(selected))


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def write_release_archive(
    root: Path,
    output: Path,
    members: tuple[str, ...],
    source_commit: str,
) -> None:
    missing = [relative for relative in members if not (root / relative).is_file()]
    if missing:
        raise FileNotFoundError("Missing release members: " + ", ".join(missing))

    file_hashes: dict[str, str] = {}
    payloads: dict[str, bytes] = {}
    for relative in members:
        payload = (root / relative).read_bytes()
        payloads[relative] = payload
        file_hashes[relative] = hashlib.sha256(payload).hexdigest()

    manifest = {
        "schema_version": "zsk-build-manifest-v1",
        "source_commit": source_commit,
        "file_count": len(members),
        "files_sha256": file_hashes,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            for relative in members:
                archive.writestr(_zip_info(relative), payloads[relative])
            archive.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    return completed.stdout


def assert_clean_git_tree() -> None:
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    if status.strip():
        raise RuntimeError("Git tree is not clean:\n" + status.rstrip())


def tracked_release_members() -> tuple[str, ...]:
    output = _git("ls-files", "-z", "--", *sorted(EXACT_MEMBERS), "schemas", "skills")
    return select_release_members(output.rstrip("\0").split("\0") if output else [])


def validate_source_package() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import install

    errors = install.validate_package(ROOT)
    if errors:
        raise RuntimeError("Source package is incomplete:\n- " + "\n- ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="从干净 Git 树构建完整 ZSK 安装包")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="ZIP 输出路径")
    args = parser.parse_args()

    assert_clean_git_tree()
    validate_source_package()
    members = tracked_release_members()
    commit = _git("rev-parse", "HEAD").strip()
    output = args.output.expanduser().resolve()
    write_release_archive(ROOT, output, members, commit)
    print(f"安装包：{output}")
    print(f"来源提交：{commit}")
    print(f"源文件数：{len(members)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
