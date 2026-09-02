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
import struct

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
_WINDOWS_POWERPOINT_DETECTION_SCRIPT = (
    "$powerPointType=[type]::GetTypeFromProgID('PowerPoint.Application');"
    "if ($null -eq $powerPointType) { exit 1 }; exit 0"
)
_WINDOWS_POWERPOINT_EXPORT_SCRIPT = r'''
param([string]$Source,[string]$Target,[string]$PidFile)
$ErrorActionPreference='Stop'
$existingPids=@(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ZskUser32 {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
'@
$app=$null
$deck=$null
$owned=$false
try {
    $app=New-Object -ComObject PowerPoint.Application
    [uint32]$processId=0
    [void][ZskUser32]::GetWindowThreadProcessId([IntPtr]$app.HWND,[ref]$processId)
    $owned=($processId -gt 0 -and $existingPids -notcontains [int]$processId)
    if ($owned) {
        $process=Get-Process -Id $processId -ErrorAction Stop
        $identity=[ordered]@{
            pid=[int]$processId
            process_name=[string]$process.ProcessName
            start_time_utc=$process.StartTime.ToUniversalTime().ToString('o')
        } | ConvertTo-Json -Compress
        [IO.File]::WriteAllText($PidFile,$identity,[Text.Encoding]::UTF8)
    }
    $deck=$app.Presentations.Open($Source,$true,$false,$false)
    $deck.SaveAs($Target,32)
} finally {
    if ($null -ne $deck) {
        try { $deck.Close() } catch {}
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($deck)
    }
    if ($owned -and $null -ne $app) { try { $app.Quit() } catch {} }
    if ($null -ne $app) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
'''
_WINDOWS_POWERPOINT_CLEANUP_SCRIPT = r'''
param([string]$IdentityFile)
$ErrorActionPreference='Stop'
if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) { exit 0 }
$identity=Get-Content -LiteralPath $IdentityFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$identity.pid -lt 1 -or [string]$identity.process_name -ne 'POWERPNT') { exit 0 }
$process=Get-Process -Id ([int]$identity.pid) -ErrorAction SilentlyContinue
if ($null -eq $process -or $process.ProcessName -ne 'POWERPNT') { exit 0 }
$startTime=$process.StartTime.ToUniversalTime().ToString('o')
if ($startTime -ne [string]$identity.start_time_utc) { exit 0 }
Stop-Process -Id ([int]$identity.pid) -Force -ErrorAction Stop
'''
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
    if suffix == ".pptx":
        native_powerpoint = _find_mac_powerpoint_automation() or _find_windows_powerpoint_automation()
        if not native_powerpoint:
            required.append("soffice/libreoffice")
    missing = tuple(name for name in required if not _find_dependency(name))
    return not missing, missing


def render_page_evidence(
    payload: bytes,
    suffix: str,
    source_id: str,
    work_root: Path,
    *,
    dpi: int = 144,
) -> RenderedPages:
    """在调用方的私有临时目录内渲染完整页集并返回内容寻址结果。"""
    suffix = suffix.lower()
    if suffix not in {".pdf", ".pptx"}:
        raise PageRenderFailed("page evidence only supports PDF and PPTX")
    if not 72 <= dpi <= 400:
        raise PageRenderFailed("page evidence DPI is outside the supported range")
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
        (pdftoppm, "-png", "-r", str(dpi), str(pdf), str(source_root / "page")),
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
        width_px, height_px = _png_dimensions(page_payload)
        artifact = PageArtifact(
            page_id=f"{source_id}-PAGE-{number:03d}",
            source_id=source_id,
            page_number=number,
            file_name=f"page-{number:03d}.png",
            sha256=digest,
            width_px=width_px,
            height_px=height_px,
        )
        rendered.append(RenderedPage(artifact, page_payload))
    return RenderedPages(tuple(rendered), expected_count, "+".join(engines))


def _pptx_to_pdf(source: Path, work_root: Path) -> tuple[Path, str]:
    osascript = _find_mac_powerpoint_automation()
    if osascript:
        return _pptx_to_pdf_with_powerpoint_mac(source, work_root, osascript), "microsoft-powerpoint"
    powershell = _find_windows_powerpoint_automation()
    if powershell:
        return _pptx_to_pdf_with_powerpoint_windows(source, work_root, powershell), "microsoft-powerpoint"
    return _pptx_to_pdf_with_libreoffice(source, work_root), "libreoffice"


def _pptx_to_pdf_with_powerpoint_windows(source: Path, work_root: Path, powershell: str) -> Path:
    script_path = work_root / "render-pptx.ps1"
    pid_path = work_root / "render-pptx.pid"
    target_pdf = work_root / "source.pdf"
    try:
        script_path.write_text(_WINDOWS_POWERPOINT_EXPORT_SCRIPT, encoding="utf-8")
        completed = subprocess.run(
            (
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(source),
                str(target_pdf),
                str(pid_path),
            ),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        _terminate_recorded_powerpoint_process(pid_path, powershell)
        raise PageRenderFailed("Microsoft PowerPoint PDF export timed out") from exc
    except OSError as exc:
        _terminate_recorded_powerpoint_process(pid_path, powershell)
        raise PageRenderFailed("Microsoft PowerPoint PDF export failed") from exc
    finally:
        try:
            script_path.unlink(missing_ok=True)
        except OSError:
            pass
    if completed.returncode != 0:
        _terminate_recorded_powerpoint_process(pid_path, powershell)
        raise PageRenderFailed("Microsoft PowerPoint PDF export failed")
    try:
        if not target_pdf.is_file() or target_pdf.stat().st_size == 0:
            _terminate_recorded_powerpoint_process(pid_path, powershell)
            raise PageRenderFailed("Microsoft PowerPoint produced no PDF")
    except OSError as exc:
        _terminate_recorded_powerpoint_process(pid_path, powershell)
        raise PageRenderFailed("Microsoft PowerPoint PDF cannot be read") from exc
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass
    return target_pdf


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


def _find_windows_powerpoint_automation() -> str | None:
    if platform.system() != "Windows":
        return None
    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return None
    try:
        completed = subprocess.run(
            (powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_POWERPOINT_DETECTION_SCRIPT),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return powershell if completed.returncode == 0 else None


def _terminate_recorded_powerpoint_process(pid_path: Path, powershell: str) -> None:
    """仅在 PID、进程名和启动时间都匹配时终止本次 PowerPoint。"""
    cleanup_path = pid_path.with_name("cleanup-pptx.ps1")
    try:
        if not pid_path.is_file():
            return
        cleanup_path.write_text(_WINDOWS_POWERPOINT_CLEANUP_SCRIPT, encoding="utf-8")
        subprocess.run(
            (
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(cleanup_path),
                str(pid_path),
            ),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        for path in (cleanup_path, pid_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


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


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 24 or not payload.startswith(_PNG_SIGNATURE) or payload[12:16] != b"IHDR":
        raise PageRenderFailed("rendered page PNG has no valid dimensions")
    width, height = struct.unpack(">II", payload[16:24])
    if width < 1 or height < 1:
        raise PageRenderFailed("rendered page PNG dimensions are invalid")
    return width, height


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
