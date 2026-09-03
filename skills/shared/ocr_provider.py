"""Local-only OCR providers. No network or hosted OCR is permitted."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import platform
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from typing import Protocol, Sequence


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

    def __init__(
        self,
        executable: str | None = None,
        languages: str = "chi_sim+eng",
        page_segmentation_mode: int | None = None,
    ) -> None:
        self.executable = executable or _tesseract_executable() or ""
        self.languages = languages
        self.page_segmentation_mode = page_segmentation_mode
        self.tessdata_directory = _tessdata_directory()
        if not self.executable:
            raise OcrUnavailable("tesseract executable is unavailable")
        if page_segmentation_mode is not None and not 0 <= page_segmentation_mode <= 13:
            raise ValueError("Tesseract page segmentation mode is invalid")

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
                if self.page_segmentation_mode is not None:
                    command.extend(("--psm", str(self.page_segmentation_mode)))
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


class AutoOcrProvider:
    """用多次本地 OCR 的一致性决定自动可用文字，不调用托管服务。"""

    name = "auto-local-ocr"

    def __init__(
        self,
        providers: Sequence[LocalOcrProvider] | None = None,
        *,
        agreement_threshold: float = 0.92,
    ) -> None:
        if not 0.0 <= agreement_threshold <= 1.0:
            raise ValueError("OCR agreement threshold is invalid")
        self.providers = tuple(providers or tuple(TesseractOcrProvider(page_segmentation_mode=mode) for mode in (3, 6, 11)))
        if len(self.providers) < 2:
            raise ValueError("automatic OCR requires at least two local passes")
        self.agreement_threshold = agreement_threshold

    def recognize(self, image: bytes) -> OcrResult:
        candidates: list[OcrResult] = []
        for provider in self.providers:
            try:
                result = provider.recognize(image)
            except (OcrUnavailable, OcrFailed):
                continue
            if result.text.strip():
                candidates.append(result)
        if not candidates:
            raise OcrFailed("all local OCR passes failed")

        ranked = sorted(candidates, key=lambda item: item.confidence, reverse=True)
        best_pair: tuple[OcrResult, OcrResult] | None = None
        best_score = -1.0
        for index, first in enumerate(ranked):
            for second in ranked[index + 1 :]:
                agreement = _text_agreement(first.text, second.text)
                score = min(first.confidence, second.confidence) * agreement
                if agreement >= self.agreement_threshold and score > best_score:
                    best_pair = (first, second)
                    best_score = score
        if best_pair is None:
            return OcrResult(ranked[0].text, 0.0, self.name)
        first, second = best_pair
        chosen = max(
            (first, second),
            key=lambda item: (len("".join(item.text.split())), item.confidence),
        )
        return OcrResult(chosen.text, min(first.confidence, second.confidence), self.name)


def default_auto_ocr_provider() -> LocalOcrProvider:
    return AutoOcrProvider()


def _text_agreement(first: str, second: str) -> float:
    normalized_first = "".join(first.split())
    normalized_second = "".join(second.split())
    if not normalized_first or not normalized_second:
        return 0.0
    return SequenceMatcher(a=normalized_first, b=normalized_second).ratio()


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
