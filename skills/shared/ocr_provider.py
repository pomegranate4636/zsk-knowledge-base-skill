"""Local-only OCR providers. No network or hosted OCR is permitted."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import platform
import shutil
import subprocess
import tempfile
from typing import Protocol


class OcrUnavailable(RuntimeError):
    pass


class OcrFailed(ValueError):
    pass


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float
    engine: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.engine.strip() or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("invalid OCR result")


class LocalOcrProvider(Protocol):
    name: str

    def recognize(self, image: bytes) -> OcrResult: ...


class TesseractOcrProvider:
    """Tesseract CLI provider using local chi_sim+eng language data."""

    name = "tesseract-local"

    def __init__(self, executable: str | None = None, languages: str = "chi_sim+eng") -> None:
        self.executable = executable or _tesseract_executable() or ""
        self.languages = languages
        self.tessdata_directory = _tessdata_directory()
        if not self.executable:
            raise OcrUnavailable("tesseract executable is unavailable")

    def recognize(self, image: bytes) -> OcrResult:
        if not image:
            raise OcrFailed("OCR image is empty")
        with tempfile.TemporaryDirectory(prefix="zsk-ocr-") as folder:
            path = Path(folder) / "page.png"
            path.write_bytes(image)
            try:
                command = [self.executable, str(path), "stdout", "-l", self.languages]
                if self.tessdata_directory is not None:
                    command.extend(("--tessdata-dir", str(self.tessdata_directory)))
                command.append("tsv")
                completed = subprocess.run(
                    tuple(command),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                    check=False,
                    shell=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OcrFailed("local OCR process failed") from exc
        if completed.returncode != 0:
            raise OcrFailed("local OCR returned a failure")
        words: list[str] = []
        weighted = 0.0
        weight = 0
        lines = completed.stdout.splitlines()
        for line in lines[1:]:
            columns = line.split("\t")
            if len(columns) < 12:
                continue
            token = columns[11].strip()
            try:
                confidence = float(columns[10])
            except ValueError:
                continue
            if not token or confidence < 0:
                continue
            words.append(token)
            token_weight = max(1, len(token))
            weighted += confidence * token_weight
            weight += token_weight
        text = " ".join(words).strip()
        score = max(0.0, min(1.0, weighted / weight / 100.0)) if weight else 0.0
        return OcrResult(text, score, self.name)


def default_local_ocr_provider() -> LocalOcrProvider:
    return TesseractOcrProvider()


def _tesseract_executable() -> str | None:
    executable = shutil.which("tesseract")
    if executable:
        return executable
    if platform.system() != "Windows":
        return None
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(key)
        if not value:
            continue
        candidate = Path(value) / "Tesseract-OCR" / "tesseract.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _tessdata_directory() -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("TESSDATA_PREFIX")
    if configured:
        candidates.append(Path(configured))
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(Path(user_profile) / ".codex" / "ocr" / "tessdata")
    for candidate in candidates:
        if (
            (candidate / "chi_sim.traineddata").is_file()
            and (candidate / "eng.traineddata").is_file()
            and (candidate / "configs" / "tsv").is_file()
        ):
            return candidate
    return None
