"""Process-local, task/page/text-bound receipts for explicitly reviewed OCR edits.

The host must call confirm only after the person approves the displayed preview.
These receipts enforce scope and freshness; they cannot prove who operated a host.
"""
from dataclasses import dataclass
import hashlib
import re
import secrets
import time
from .contracts import SOURCE_ID, TASK_ID

@dataclass(frozen=True)
class CorrectionPreview:
    confirmation: str
    task_id: str
    source_id: str
    page_number: int
    page_sha256: str
    correction: str

class OcrReviewGate:
    def __init__(self) -> None:
        self._pending: dict[str, tuple[tuple, float]] = {}
        self._approved: dict[str, tuple[tuple, float]] = {}

    @staticmethod
    def _scope(task_id: str, source_id: str, page_number: int, page_sha256: str, correction: str) -> tuple:
        if not TASK_ID.fullmatch(task_id) or not SOURCE_ID.fullmatch(source_id):
            raise ValueError('OCR review requires valid task and source IDs')
        if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1 or not re.fullmatch(r'[0-9a-f]{64}', page_sha256):
            raise ValueError('OCR review requires an exact page and image hash')
        if not isinstance(correction, str) or not correction.strip():
            raise ValueError('OCR correction is empty')
        return (task_id, source_id, page_number, page_sha256, hashlib.sha256(correction.strip().encode('utf-8')).hexdigest())

    def _prune(self) -> None:
        now=time.monotonic()
        self._pending={key:value for key,value in self._pending.items() if 0 <= now-value[1] < 900}
        self._approved={key:value for key,value in self._approved.items() if 0 <= now-value[1] < 900}

    def preview(self, task_id: str, source_id: str, page_number: int, page_sha256: str, correction: str) -> CorrectionPreview:
        self._prune()
        scope=self._scope(task_id,source_id,page_number,page_sha256,correction)
        token=secrets.token_urlsafe(32)
        self._pending[token]=(scope,time.monotonic())
        return CorrectionPreview(token,task_id,source_id,page_number,page_sha256,correction.strip())

    def confirm(self, confirmation: str) -> str:
        self._prune()
        issued=self._pending.pop(confirmation,None)
        if issued is None:
            raise ValueError('OCR correction preview is unknown, consumed or expired')
        receipt=secrets.token_urlsafe(32)
        self._approved[receipt]=(issued[0],time.monotonic())
        return receipt

    def consume(self, receipt: str | None, task_id: str | None, source_id: str, page_number: int, page_sha256: str, correction: str) -> bool:
        self._prune()
        issued=self._approved.get(receipt)
        if issued is None or task_id is None:
            return False
        if issued[0] != self._scope(task_id,source_id,page_number,page_sha256,correction):
            return False
        del self._approved[receipt]
        return True
