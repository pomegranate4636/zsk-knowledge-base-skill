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
    "markitdown-skill",
    "shared",
)
MARKITDOWN_SPEC = "markitdown[docx,pdf,pptx,xlsx]==0.1.6"
MARKITDOWN_INSTALL_TIMEOUT_SECONDS = 600


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
    if not (source_root / "shared" / "page_renderer.py").is_file():
        errors.append("缺少可选页级证据模块：shared/page_renderer.py")
    return errors


def installed_state(destination: Path) -> tuple[list[str], list[str]]:
    present: list[str] = []
    missing: list[str] = []
    for name in COMPONENTS:
        target = destination / name
        valid = target.is_dir() and (
            (target / "markdown_converter.py").is_file() and (target / "page_renderer.py").is_file()
            if name == "shared" else (target / "SKILL.md").is_file()
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


def install_converter() -> bool:
    pipx = shutil.which("pipx")
    if not pipx:
        print("未找到 pipx，无法自动安装 MarkItDown。请先安装 pipx 后重试。", file=sys.stderr)
        return False
    command = (pipx, "inject", "--force", "markitdown", MARKITDOWN_SPEC) if converter_version() else (pipx, "install", MARKITDOWN_SPEC)
    print("正在安装 MarkItDown 文档转换依赖，首次下载可能需要几分钟……")
    try:
        completed = subprocess.run(
            command,
            timeout=MARKITDOWN_INSTALL_TIMEOUT_SECONDS,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        print("MarkItDown 下载超过 10 分钟，已停止。请检查网络后重新执行安装。", file=sys.stderr)
        return False
    except OSError as exc:
        print(f"无法启动 MarkItDown 安装命令：{exc}", file=sys.stderr)
        return False
    if completed.returncode != 0:
        print(f"MarkItDown 安装失败（退出码 {completed.returncode}）。", file=sys.stderr)
        _print_command_output(completed)
        print("请检查网络、pipx 和安全软件拦截记录后重试。", file=sys.stderr)
        return False
    version = converter_version()
    if not version:
        print("MarkItDown 安装命令已结束，但转换器仍不可用。请重新打开终端后运行 --doctor。", file=sys.stderr)
        return False
    return True


def _print_command_output(completed: subprocess.CompletedProcess[str]) -> None:
    lines = [line for line in (completed.stderr + "\n" + completed.stdout).splitlines() if line.strip()]
    if not lines:
        print("安装工具没有返回可读错误。", file=sys.stderr)
        return
    print("安装工具返回：", file=sys.stderr)
    for line in lines[-20:]:
        print(f"  {line}", file=sys.stderr)


def rollback_fresh_install(destination: Path) -> None:
    """回滚本次刚复制的 ZSK 组件；只在 install() 已完整成功后调用。"""
    for name in reversed(COMPONENTS):
        target = destination / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)


def page_evidence_status() -> dict[str, bool]:
    pdf_ready = _optional_binary_ready(("pdftoppm",), ("-v",)) and _optional_binary_ready(("pdfinfo",), ("-v",))
    ppt_ready = bool(pdf_ready and _optional_binary_ready(("soffice", "libreoffice"), ("--version",)))
    return {"pdf": pdf_ready, "pptx": ppt_ready}


def _optional_binary_ready(names: tuple[str, ...], version_args: tuple[str, ...]) -> bool:
    for name in names:
        executable = shutil.which(name)
        if not executable:
            continue
        try:
            completed = subprocess.run(
                (executable, *version_args), capture_output=True, text=True, timeout=15, check=False, shell=False
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0:
            return True
    return False


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

    return 0


def print_install_success(destination: Path) -> None:
    present, _ = installed_state(destination)
    print(f"安装完成：{destination}")
    print("已安装：" + "、".join(present))
    print("请重新打开一个 Codex / WorkBuddy 任务，再检查 zsk-router。")


def main() -> int:
    parser = argparse.ArgumentParser(description="安装完整的 ZSK 知识库 Skill 组合")
    parser.add_argument("--dest", type=Path, default=default_destination(), help="目标 Skills 目录")
    parser.add_argument("--check", action="store_true", help="只检查目标目录，不写入")
    parser.add_argument("--doctor", action="store_true", help="检查完整组件与 MarkItDown 转换器，不写入")
    parser.add_argument("--install-markitdown", action="store_true", help="安装或补齐 MarkItDown 最小格式依赖")
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
        pages = page_evidence_status()
        print("组件：" + ("齐全" if not missing else "缺少 " + "、".join(missing)))
        print("MarkItDown：" + (version or "不可用"))
        print("页级证据（可选）：PDF " + ("可用" if pages["pdf"] else "不可用") + "；PPTX " + ("可用" if pages["pptx"] else "不可用"))
        return 0 if not missing and version else 1

    source_root = Path(__file__).resolve().parent / "skills"
    result = install(source_root, destination)
    if result != 0:
        return result
    if not args.install_markitdown:
        print_install_success(destination)
        return 0
    if not install_converter():
        rollback_fresh_install(destination)
        print("MarkItDown 未就绪，本次新增的 ZSK 组件已回滚；没有留下半安装状态。", file=sys.stderr)
        return 6
    print_install_success(destination)
    print("MarkItDown 已就绪：" + (converter_version() or "未知版本"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
