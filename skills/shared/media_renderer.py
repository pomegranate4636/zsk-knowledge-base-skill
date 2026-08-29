"""Page-image rendering for PDF/PPT sources.

Visual pages are first-class source evidence.  Text conversion remains useful for
search and drafting, but it never replaces the page images for rich documents.
The renderer uses local executables only and never downloads or calls a model.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile

from .contracts import MediaArtifact


class MediaRendererUnavailable(RuntimeError):
    """No safe local page renderer is available."""


class MediaRenderFailed(ValueError):
    """A rich document could not be rendered into a complete page set."""


@dataclass(frozen=True)
class RenderedMedia:
    artifacts: tuple[MediaArtifact, ...]
    page_count: int
    engine: str
    ocr_markdown: str = ""


_PAGE_NUMBER = re.compile(r"-(\d+)\.png$", re.IGNORECASE)


def _run_process(argv: tuple[str, ...], *, failure: str, **kwargs) -> subprocess.CompletedProcess[str]:
    if kwargs.get("text") is True:
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    try:
        return subprocess.run(argv, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaRenderFailed(failure) from exc


def _page_number(path: Path) -> int:
    match = _PAGE_NUMBER.search(path.name)
    if not match:
        raise MediaRenderFailed("rendered page name has no numeric page suffix")
    return int(match.group(1))


def _windows_ocr(image: Path, powershell: str) -> str:
    script = r'''$OutputEncoding=[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.FileAccessMode,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object {$_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'})[0]
function Await-Result($operation,$type) {$task=$asTask.MakeGenericMethod($type).Invoke($null,@($operation));$task.Wait();$task.Result}
$file=Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:ZSK_OCR_IMAGE)) ([Windows.Storage.StorageFile])
$stream=Await-Result ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder=Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap=Await-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('zh-Hans-CN'))
if($null -eq $engine){$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()}
if($null -eq $engine){throw 'windows_ocr_language_missing'}
$result=Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($result.Text))'''
    environment = dict(os.environ)
    environment["ZSK_OCR_IMAGE"] = str(image.resolve())
    completed = _run_process(
        (powershell, "-NoProfile", "-NonInteractive", "-Command", script), env=environment,
        failure="Windows page OCR failed", capture_output=True, text=True, timeout=120, check=False, shell=False,
    )
    if completed.returncode != 0:
        raise MediaRenderFailed("Windows page OCR failed")
    try:
        return base64.b64decode(completed.stdout.strip(), validate=True).decode("utf-8").strip()
    except (ValueError, UnicodeError) as exc:
        raise MediaRenderFailed("Windows page OCR output is invalid") from exc


def _ocr_page(image: Path, work_dir: Path) -> tuple[str, str]:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell or os.name != "nt":
            raise MediaRendererUnavailable("Tesseract or Windows OCR is required for PDF/PPTX pages")
        return _windows_ocr(image, powershell), "windows-ocr"
    output = work_dir / f"ocr-{_page_number(image):03d}"
    language = os.environ.get("ZSK_OCR_LANG", "chi_sim+eng")
    completed = _run_process(
        (tesseract, str(image), str(output), "-l", language),
        failure="page OCR failed", capture_output=True, text=True, timeout=120, check=False, shell=False,
    )
    text_path = output.with_suffix(".txt")
    if completed.returncode != 0 or not text_path.is_file():
        raise MediaRenderFailed("page OCR failed")
    try:
        text = text_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()
        return text, "tesseract"
    except UnicodeError as exc:
        raise MediaRenderFailed("page OCR output is not UTF-8") from exc


def render_pages(payload: bytes, suffix: str, source_id: str, output_dir: Path) -> RenderedMedia:
    """Render every PDF/PPTX page to PNG in *output_dir*.

    ``pdftoppm`` is preferred for PDF.  PPTX is converted to PDF with a local
    LibreOffice/soffice executable and then rendered by the same path.
    """
    suffix = suffix.lower()
    if suffix not in {".pdf", ".pptx"}:
        return RenderedMedia((), 0, "not_required", "")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".zsk-media-", dir=output_dir) as folder:
        root = Path(folder)
        source = root / f"source{suffix}"
        source.write_bytes(payload)
        pdf = source
        engine = "pdftoppm"
        if suffix == ".pptx":
            pdf = root / "source.pdf"
            engine = _render_pptx_to_pdf(source, pdf, root)
        pdftoppm = shutil.which("pdftoppm")
        if not pdftoppm:
            raise MediaRendererUnavailable("pdftoppm is required to render PDF/PPTX pages")
        prefix = root / "page"
        completed = _run_process(
            (pdftoppm, "-png", "-r", "150", str(pdf), str(prefix)),
            failure="PDF/PPTX page rendering failed", capture_output=True, text=True, timeout=300, check=False, shell=False,
        )
        pages = tuple(sorted(root.glob("page-*.png"), key=_page_number))
        if completed.returncode != 0 or not pages:
            raise MediaRenderFailed("PDF/PPTX produced no page images")
        artifacts: list[MediaArtifact] = []
        ocr_sections: list[str] = []
        ocr_engines: set[str] = set()
        for number, image in enumerate(pages, 1):
            ocr_text, ocr_engine = _ocr_page(image, root)
            ocr_engines.add(ocr_engine)
            target = output_dir / f"page-{number:03d}.png"
            target.write_bytes(image.read_bytes())
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            ocr_digest = hashlib.sha256(ocr_text.encode("utf-8")).hexdigest()
            artifacts.append(MediaArtifact(f"{source_id}-PAGE-{number:03d}", source_id, number, "image", target.name, digest, ocr_text_sha256=ocr_digest))
            ocr_sections.append(f"## 第 {number} 页\n\n{ocr_text or '[本页未识别到文字]'}")
    return RenderedMedia(tuple(artifacts), len(artifacts), engine + "+" + "+".join(sorted(ocr_engines)), "\n\n".join(ocr_sections) + "\n")


def _render_pptx_to_pdf(source: Path, pdf: Path, work_dir: Path) -> str:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        completed = _run_process(
            (soffice, "--headless", "--convert-to", "pdf", "--outdir", str(work_dir), str(source)),
            failure="PPTX to PDF rendering failed", capture_output=True, text=True, timeout=180, check=False, shell=False,
        )
        if completed.returncode == 0 and pdf.is_file():
            return "libreoffice+pdftoppm"
        raise MediaRenderFailed("PPTX to PDF rendering failed")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    powerpoint = Path(r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE")
    if not powershell or not powerpoint.is_file():
        raise MediaRendererUnavailable("LibreOffice or Microsoft PowerPoint is required to render PPTX pages")
    script_path = work_dir / "render-pptx.ps1"
    pid_path = work_dir / "render-pptx.pid"
    script_path.write_text(
        "param([string]$Source,[string]$Target,[string]$PidFile)\n"
        "$ErrorActionPreference='Stop'\n"
        "Add-Type @'\nusing System;\nusing System.Runtime.InteropServices;\npublic static class ZskUser32 { [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId); }\n'@\n"
        "$app=New-Object -ComObject PowerPoint.Application\n"
        "$processId=0;[void][ZskUser32]::GetWindowThreadProcessId([IntPtr]$app.HWND,[ref]$processId);[IO.File]::WriteAllText($PidFile,[string]$processId)\n"
        "try{$deck=$app.Presentations.Open($Source,$true,$false,$false);$deck.SaveAs($Target,32);$deck.Close()}finally{$app.Quit()}\n",
        encoding="utf-8",
    )
    try:
        completed = _run_process(
            (powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script_path), str(source), str(pdf), str(pid_path)),
            failure="PowerPoint to PDF rendering failed", capture_output=True, text=True, timeout=180, check=False, shell=False,
        )
    except MediaRenderFailed:
        _terminate_recorded_process(pid_path)
        raise
    if completed.returncode != 0 or not pdf.is_file():
        _terminate_recorded_process(pid_path)
        diagnostic = (completed.stderr or completed.stdout or "no diagnostic output").strip()
        raise MediaRenderFailed(f"PowerPoint to PDF rendering failed: {diagnostic[-1200:]}")
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass
    return "powerpoint+pdftoppm"


def _terminate_recorded_process(pid_path: Path) -> None:
    """Terminate only the COM server PID recorded by this renderer instance."""
    try:
        raw = pid_path.read_text(encoding="ascii").strip()
        if not raw.isdigit() or int(raw) < 1:
            return
        os.kill(int(raw), signal.SIGTERM)
    except (FileNotFoundError, OSError, ValueError):
        pass
    finally:
        try:
            pid_path.unlink()
        except OSError:
            pass
