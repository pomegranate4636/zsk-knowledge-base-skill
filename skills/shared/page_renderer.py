"""通用 PDF/PPTX 完整页证据渲染；只使用本机程序，不调用模型。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shutil
import subprocess

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


def renderer_status(suffix: str) -> tuple[bool, tuple[str, ...]]:
    """返回指定格式的可选页级证据依赖状态。"""
    suffix = suffix.lower()
    required = ["pdftoppm", "pdfinfo"]
    if suffix == ".pptx":
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
        pdf = _pptx_to_pdf(source, source_root)
        engines.append("libreoffice")
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


def _pptx_to_pdf(source: Path, work_root: Path) -> Path:
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
