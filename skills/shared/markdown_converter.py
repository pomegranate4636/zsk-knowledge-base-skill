"""ZSK 的单一富文档 Markdown 转换器：只调用本机 MarkItDown。"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


MARKITDOWN_SUFFIXES = frozenset({".docx", ".pptx", ".xlsx", ".pdf", ".html", ".htm", ".json"})
_PPTX_SLIDE_COMMENT = re.compile(r"(?m)^<!--[ \t]*Slide number:[ \t]*(\d+)[ \t]*-->[ \t]*$")


class ConverterUnavailable(Exception):
    """本机没有可运行的 MarkItDown。"""


class ConversionFailed(ValueError):
    """MarkItDown 不能安全地产生非空 Markdown。"""


@dataclass(frozen=True)
class MarkdownConversion:
    text: str
    engine: str
    version: str


def convert_to_markdown(payload: bytes, suffix: str) -> MarkdownConversion:
    """把一个允许的富文档转换为 Markdown；不联网、不调用模型、不保留临时原件。"""
    suffix = suffix.lower()
    if suffix not in MARKITDOWN_SUFFIXES:
        raise ConversionFailed("unsupported MarkItDown suffix")
    executable = _executable()
    version = _version(executable)
    with tempfile.TemporaryDirectory(prefix="zsk-markitdown-") as folder:
        source = Path(folder) / f"source{suffix}"
        output = Path(folder) / "readable.md"
        source.write_bytes(payload)
        try:
            completed = subprocess.run(
                (executable, str(source), "-o", str(output)),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConversionFailed("MarkItDown did not complete safely") from exc
        if completed.returncode != 0 or not output.is_file():
            raise ConversionFailed("MarkItDown conversion failed")
        try:
            text = output.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise ConversionFailed("MarkItDown output is not UTF-8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if suffix == ".pptx":
        text = normalize_pptx_slide_markers(text)
    if not text or "\x00" in text:
        raise ConversionFailed("MarkItDown produced no safe readable text")
    return MarkdownConversion(text + "\n", "markitdown", version)


def normalize_pptx_slide_markers(text: str) -> str:
    """把 MarkItDown 的隐藏页码注释转换成可见、可引用的页标题。"""
    return _PPTX_SLIDE_COMMENT.sub(lambda match: f"## 第 {match.group(1)} 页", text)


def markitdown_status() -> MarkdownConversion | None:
    """供安装检查使用；只返回可执行版本，不处理任何客户资料。"""
    try:
        executable = _executable()
        return MarkdownConversion("", "markitdown", _version(executable))
    except ConverterUnavailable:
        return None


def _executable() -> str:
    configured = os.environ.get("ZSK_MARKITDOWN_BIN")
    executable = configured if configured and os.path.isabs(configured) else shutil.which("markitdown")
    if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise ConverterUnavailable("MarkItDown is required for rich document intake")
    return executable


@lru_cache(maxsize=4)
def _version(executable: str) -> str:
    try:
        completed = subprocess.run(
            (executable, "--version"), capture_output=True, text=True, timeout=15, check=False, shell=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConverterUnavailable("MarkItDown version cannot be checked") from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise ConverterUnavailable("MarkItDown version cannot be checked")
    return value
