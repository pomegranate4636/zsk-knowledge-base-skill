#!/usr/bin/env python3
"""Install the complete ZSK skill bundle without overwriting existing data."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


COMPONENTS = (
    "zsk-router",
    "zsk-ruku",
    "zsk-zhishi",
    "zsk-duibiao",
    "zsk-profile",
    "shared",
)


def default_destination() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "skills"
    return Path.home() / ".codex" / "skills"


def validate_source(source_root: Path) -> list[str]:
    errors: list[str] = []
    for name in COMPONENTS:
        component = source_root / name
        if not component.is_dir():
            errors.append(f"缺少组件目录：{component}")
        if name != "shared" and not (component / "SKILL.md").is_file():
            errors.append(f"缺少 Skill 入口：{component / 'SKILL.md'}")
    if not (source_root / "shared" / "markdown_converter.py").is_file():
        errors.append("缺少统一 Markdown 转换模块：shared/markdown_converter.py")
    return errors


def installed_state(destination: Path) -> tuple[list[str], list[str]]:
    present: list[str] = []
    missing: list[str] = []
    for name in COMPONENTS:
        target = destination / name
        valid = target.is_dir() and (
            (target / "markdown_converter.py").is_file() if name == "shared" else (target / "SKILL.md").is_file()
        )
        (present if valid else missing).append(name)
    return present, missing


def converter_version() -> str | None:
    executable = shutil.which("markitdown")
    if not executable:
        return None
    try:
        completed = subprocess.run((executable, "--version"), capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() if completed.returncode == 0 and completed.stdout.strip() else None


def install(source_root: Path, destination: Path) -> int:
    source_errors = validate_source(source_root)
    if source_errors:
        print("安装包不完整，已停止：", file=sys.stderr)
        for error in source_errors:
            print(f"- {error}", file=sys.stderr)
        return 2

    conflicts = [destination / name for name in COMPONENTS if (destination / name).exists()]
    if conflicts:
        print("发现已有同名目录。为避免覆盖，安装已停止：", file=sys.stderr)
        for conflict in conflicts:
            print(f"- {conflict}", file=sys.stderr)
        print("请先让 Codex 检查这些目录，再决定保留、备份或更新。", file=sys.stderr)
        return 3

    destination.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for name in COMPONENTS:
            target = destination / name
            shutil.copytree(source_root / name, target)
            created.append(target)
    except Exception as exc:
        for target in reversed(created):
            shutil.rmtree(target, ignore_errors=True)
        print(f"安装失败，已回滚本次新增目录：{exc}", file=sys.stderr)
        return 4

    present, missing = installed_state(destination)
    if missing:
        print(f"安装后检查失败，缺少：{', '.join(missing)}", file=sys.stderr)
        return 5

    print(f"安装完成：{destination}")
    print("已安装：" + "、".join(present))
    print("请重新打开一个 Codex / WorkBuddy 任务，再检查 zsk-router。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="安装完整的 ZSK 知识库 Skill 组合")
    parser.add_argument("--dest", type=Path, default=default_destination(), help="目标 Skills 目录")
    parser.add_argument("--check", action="store_true", help="只检查目标目录，不写入")
    parser.add_argument("--doctor", action="store_true", help="检查完整组件与 MarkItDown 转换器，不写入")
    args = parser.parse_args()

    destination = args.dest.expanduser().resolve()
    if args.check:
        present, missing = installed_state(destination)
        print(f"检查目录：{destination}")
        print("已存在：" + ("、".join(present) if present else "无"))
        print("缺少：" + ("、".join(missing) if missing else "无"))
        return 0 if not missing else 1

    if args.doctor:
        present, missing = installed_state(destination)
        version = converter_version()
        print("组件：" + ("齐全" if not missing else "缺少 " + "、".join(missing)))
        print("MarkItDown：" + (version or "不可用"))
        return 0 if not missing and version else 1

    source_root = Path(__file__).resolve().parent / "skills"
    return install(source_root, destination)


if __name__ == "__main__":
    raise SystemExit(main())
