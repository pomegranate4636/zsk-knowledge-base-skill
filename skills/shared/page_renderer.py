"""通用 PDF/PPTX 完整页证据渲染；只使用本机程序，不调用模型。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import platform
from pathlib import Path
import re
import shutil
import stat
import subprocess
import uuid

from .contracts import PageArtifact


class PageRendererUnavailable(RuntimeError):
    """当前电脑缺少可选的页级证据依赖。"""


class PageRenderFailed(ValueError):
    """页面无法完整、连续地渲染。"""


@dataclass(frozen=True)
class RenderedPage:
    artifact: PageArtifact
    payload: bytes


@dataclass(frozen=True)
class RenderedPages:
    pages: tuple[RenderedPage, ...]
    page_count: int
    engine: str


_PAGE_NAME = re.compile(r"^page-(\d+)\.png$", re.IGNORECASE)
_PDFINFO_PAGES = re.compile(r"(?m)^Pages:\s*(\d+)\s*$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAC_POWERPOINT = Path("/Applications/Microsoft PowerPoint.app")
_POWERPOINT_EXPORT_SCRIPT = r'''
on run argv
    set inputPath to POSIX file (item 1 of argv) as alias
    set outputPosix to item 2 of argv
    set outputPath to (get POSIX file outputPosix as string)
    set openedPresentation to missing value
    try
        tell application "Microsoft PowerPoint"
            launch
            open inputPath
            repeat 60 times
                try
                    if (count of slides of active presentation) > 0 then
                        set openedPresentation to active presentation
                        exit repeat
                    end if
                end try
                delay 0.5
            end repeat
            if openedPresentation is missing value then error "PowerPoint did not finish opening the presentation"
            set slideCount to count of slides of openedPresentation
            save openedPresentation in outputPath as save as PDF
        end tell
        repeat 120 times
            if (do shell script "test -s " & quoted form of outputPosix & " && echo yes || echo no") is "yes" then exit repeat
            delay 0.5
        end repeat
        if (do shell script "test -s " & quoted form of outputPosix & " && echo yes || echo no") is not "yes" then error "PowerPoint produced no PDF"
        tell application "Microsoft PowerPoint" to close openedPresentation
        return slideCount
    on error errText number errNumber
        try
            if openedPresentation is not missing value then tell application "Microsoft PowerPoint" to close openedPresentation
        end try
        error errText number errNumber
    end try
end run
'''


def renderer_status(suffix: str) -> tuple[bool, tuple[str, ...]]:
    """返回指定格式的可选页级证据依赖状态。"""
    suffix = suffix.lower()
    required = ["pdftoppm", "pdfinfo"]
    if suffix == ".pptx" and not _find_mac_powerpoint_automation():
        required.append("soffice/libreoffice")
    missing = tuple(name for name in required if not _find_dependency(name))
    return not missing, missing


def render_page_evidence(payload: bytes, suffix: str, source_id: str, work_root: Path) -> RenderedPages:
    """在调用方的私有临时目录内渲染完整页集并返回内容寻址结果。"""
    suffix = suffix.lower()
    if suffix not in {".pdf", ".pptx"}:
        raise PageRenderFailed("page evidence only supports PDF and PPTX")
    ready, missing = renderer_status(suffix)
    if not ready:
        raise PageRendererUnavailable("missing page renderer: " + ", ".join(missing))
    source_root = work_root / source_id
    try:
        source_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        source = source_root / f"source{suffix}"
        source.write_bytes(payload)
    except OSError as exc:
        raise PageRenderFailed("private page workspace cannot be created") from exc

    pdf = source
    engines: list[str] = []
    if suffix == ".pptx":
        pdf, pptx_engine = _pptx_to_pdf(source, source_root)
        engines.append(pptx_engine)
    expected_count = _pdf_page_count(pdf)
    pdftoppm = _find_dependency("pdftoppm")
    assert pdftoppm is not None
    _run(
        (pdftoppm, "-png", "-r", "144", str(pdf), str(source_root / "page")),
        timeout=300,
        failure="page rendering failed",
    )
    engines.append("pdftoppm")

    rendered: list[RenderedPage] = []
    page_files = tuple(sorted(source_root.glob("page-*.png"), key=_page_number))
    numbers = [_page_number(path) for path in page_files]
    if expected_count < 1 or numbers != list(range(1, expected_count + 1)):
        raise PageRenderFailed("rendered pages are missing, duplicated, or out of order")
    for number, path in zip(numbers, page_files, strict=True):
        try:
            page_payload = path.read_bytes()
        except OSError as exc:
            raise PageRenderFailed("rendered page cannot be read") from exc
        if not page_payload.startswith(_PNG_SIGNATURE):
            raise PageRenderFailed("rendered page is not a valid PNG")
        digest = hashlib.sha256(page_payload).hexdigest()
        artifact = PageArtifact(
            page_id=f"{source_id}-PAGE-{number:03d}",
            source_id=source_id,
            page_number=number,
            file_name=f"page-{number:03d}.png",
            sha256=digest,
        )
        rendered.append(RenderedPage(artifact, page_payload))
    return RenderedPages(tuple(rendered), expected_count, "+".join(engines))


def _pptx_to_pdf(source: Path, work_root: Path) -> tuple[Path, str]:
    osascript = _find_mac_powerpoint_automation()
    if osascript:
        return _pptx_to_pdf_with_powerpoint_mac(source, work_root, osascript), "microsoft-powerpoint"
    return _pptx_to_pdf_with_libreoffice(source, work_root), "libreoffice"


def _pptx_to_pdf_with_powerpoint_mac(source: Path, work_root: Path, osascript: str) -> Path:
    cache_root = Path.home() / "Library" / "Caches" / "ZSKPowerPointRenderer"
    try:
        cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        mode = os.lstat(cache_root).st_mode
        if not _is_safe_directory(mode):
            raise PageRenderFailed("PowerPoint render cache is not a safe directory")
        os.chmod(cache_root, 0o700)
    except OSError as exc:
        raise PageRenderFailed("PowerPoint render cache cannot be created") from exc
    cache_pdf = cache_root / f"{uuid.uuid4().hex}.pdf"
    target_pdf = work_root / "source.pdf"
    try:
        completed = subprocess.run(
            (osascript, "-e", _POWERPOINT_EXPORT_SCRIPT, str(source), str(cache_pdf)),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            denied = "-1743" in completed.stderr or "not authorized to send apple events" in completed.stderr.lower()
            if denied:
                raise PageRendererUnavailable("macOS automation permission is required for Microsoft PowerPoint")
            raise PageRenderFailed("Microsoft PowerPoint PDF export failed")
        if not cache_pdf.is_file() or cache_pdf.stat().st_size == 0:
            raise PageRenderFailed("Microsoft PowerPoint produced no PDF")
        shutil.move(str(cache_pdf), target_pdf)
    except subprocess.TimeoutExpired as exc:
        raise PageRenderFailed("Microsoft PowerPoint PDF export timed out") from exc
    except OSError as exc:
        raise PageRenderFailed("Microsoft PowerPoint PDF cannot be read") from exc
    finally:
        cache_pdf.unlink(missing_ok=True)
    return target_pdf


def _pptx_to_pdf_with_libreoffice(source: Path, work_root: Path) -> Path:
    soffice = _find_dependency("soffice/libreoffice")
    if not soffice:
        raise PageRendererUnavailable("LibreOffice is required for PPTX page evidence")
    profile = work_root / "libreoffice-profile"
    profile.mkdir(mode=0o700, exist_ok=False)
    _run(
        (
            soffice,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work_root),
            str(source),
        ),
        timeout=180,
        failure="PPTX to PDF conversion failed",
    )
    pdf = work_root / "source.pdf"
    if not pdf.is_file():
        raise PageRenderFailed("PPTX conversion produced no PDF")
    return pdf


def _find_mac_powerpoint_automation() -> str | None:
    if platform.system() != "Darwin" or not _MAC_POWERPOINT.is_dir():
        return None
    return shutil.which("osascript")


def _is_safe_directory(mode: int) -> bool:
    return not stat.S_ISLNK(mode) and stat.S_ISDIR(mode)


def _pdf_page_count(pdf: Path) -> int:
    pdfinfo = _find_dependency("pdfinfo")
    if not pdfinfo:
        raise PageRendererUnavailable("pdfinfo is required for page count verification")
    completed = _run((pdfinfo, str(pdf)), timeout=60, failure="PDF page count failed")
    match = _PDFINFO_PAGES.search(completed.stdout)
    if not match or int(match.group(1)) < 1:
        raise PageRenderFailed("PDF page count is unavailable")
    return int(match.group(1))


def _page_number(path: Path) -> int:
    match = _PAGE_NAME.fullmatch(path.name)
    if not match:
        raise PageRenderFailed("rendered page name is invalid")
    return int(match.group(1))


def _find_dependency(name: str) -> str | None:
    if name == "soffice/libreoffice":
        candidates = (shutil.which("soffice"), shutil.which("libreoffice"))
        version_args = ("--version",)
    else:
        candidates = (shutil.which(name),)
        version_args = ("-v",) if name in {"pdftoppm", "pdfinfo"} else ("--version",)
    for executable in candidates:
        if not executable:
            continue
        try:
            completed = subprocess.run(
                (executable, *version_args), capture_output=True, text=True, timeout=15, check=False, shell=False
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0:
            return executable
    return None


def _run(argv: tuple[str, ...], *, timeout: int, failure: str) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PageRenderFailed(failure) from exc
    if completed.returncode != 0:
        raise PageRenderFailed(failure)
    return completed
