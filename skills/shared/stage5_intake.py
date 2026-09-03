"""阶段 5 的零安装 01/02 入库闭环。"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import io
import json
from pathlib import Path, PurePath
import re
import tempfile
from typing import Any, Mapping

from .adapter import KnowledgeBaseAdapter
from .contracts import SOURCE_ROLES, SOURCE_SCHEMA, TASK_ID, BackendObjectRef, Binding, ExceptionRecord, PageArtifact, PageTextEvidence, SourceRecord
from .markdown_converter import ConversionFailed, ConverterUnavailable, MarkdownConversion, convert_to_markdown
from .naming import human_source_label, page_file_name
from .page_renderer import PageRenderFailed, PageRendererUnavailable, RenderedPage, render_page_evidence
from .ocr_provider import LocalOcrProvider, OcrFailed, OcrUnavailable
from .page_text import PageTextFailed, build_page_text_evidence


SUPPORTED_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json", ".html", ".htm", ".docx", ".pptx", ".xlsx", ".pdf"})
_TABLE_SUFFIXES = frozenset({".csv", ".xlsx"})
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_IDENTITY = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\w)")
_SENSITIVE = (_PHONE, _EMAIL, _IDENTITY)
_SAFE_NOTES = {
    "format_unsupported": "当前版本不支持该格式（或缺少该格式的可选依赖），未保存原件或正文。",
    "source_unreadable": "资料为空或无法安全读取，未保存原件或正文。",
    "conversion_failed": "资料无法按严格规则转换，未保存原件或正文。",
    "page_evidence_unavailable": "当前电脑缺少可选的完整页证据能力，未保存原件、正文或页图。",
    "page_evidence_failed": "完整页证据生成或校验失败，未报告登记成功。",
    "ocr_unavailable": "当前电脑缺少本地 OCR Provider，图片页未进入知识库。",
    "ocr_failed": "本地 OCR 执行失败，图片页未进入知识库。",
    "file_quality_insufficient": "资料文字无法被本机自动可靠还原，未写入知识库。",
    "permission_denied": "资料处理权未获允许，未保存原件或正文。",
    "ownership_unknown": "资料归属尚未确认，未保存原件或正文。",
    "privacy_approval_required": "检测到敏感信息，尚未取得原件保存授权。",
    "duplicate_conflict": "相同资料的客户或来源角色存在冲突。",
    "version_conflict": "同名资料的版本关系尚未确认。",
    "write_failed": "资料写入未完整完成，已停止后续处理。",
    "readback_failed": "资料写后回读失败，已停止后续处理。",
}
_QUESTIONS = {
    "privacy_approval_required": "是否允许在该私有知识库中保存敏感原件？",
    "version_conflict": "请确认它是否为已有来源的新版本。",
}


def _sensitive_count(*texts: str) -> int:
    return sum(len(pattern.findall(text)) for text in texts for pattern in _SENSITIVE)


def _redact_sensitive(text: str) -> str:
    for pattern in _SENSITIVE:
        text = pattern.sub("[已脱敏]", text)
    return text


def _redact_page_text(item: PageTextEvidence) -> PageTextEvidence:
    return PageTextEvidence.create(
        item.source_id,
        item.page_number,
        item.page_sha256,
        native_text=_redact_sensitive(item.native_text),
        ocr_text=_redact_sensitive(item.ocr_text),
        verbatim_text=_redact_sensitive(item.verbatim_text),
        text_source=item.text_source,
        confidence=item.confidence,
        review_status=item.review_status,
    )


@dataclass(frozen=True)
class IntakeRequest:
    task_id: str
    binding: Binding
    file_name: str
    payload: bytes
    source_title: str
    source_role: str = "unknown"
    permission_status: str = "allowed"
    original_retention_approved: bool = False
    stable_source_locator: str | None = None
    confirmed_version_of: str | None = None
    page_evidence_mode: str = "off"
    ocr_corrections: Mapping[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not TASK_ID.fullmatch(self.task_id):
            raise ValueError("task_id must be a real Codex task UUID")
        if not self.file_name or self.file_name != PurePath(self.file_name).name or "/" in self.file_name or "\\" in self.file_name:
            raise ValueError("file_name must be a plain file name")
        if not isinstance(self.payload, bytes) or not self.source_title.strip():
            raise ValueError("payload bytes and source_title are required")
        if self.source_role not in SOURCE_ROLES:
            raise ValueError("source_role is unsupported")
        if self.permission_status not in {"allowed", "unknown", "denied"}:
            raise ValueError("permission_status is unsupported")
        if self.page_evidence_mode not in {"off", "required"}:
            raise ValueError("page_evidence_mode is unsupported")
        if any(not isinstance(page, int) or page < 1 or not isinstance(text, str) for page, text in self.ocr_corrections.items()):
            raise ValueError("OCR corrections must map positive page numbers to text")


@dataclass(frozen=True)
class IntakeResponse:
    status: str
    code: str | None
    source_id: str
    refs: tuple[BackendObjectRef, ...]
    record: SourceRecord | None
    evidence: dict[str, Any]


class Stage5Intake:
    """只登记 01 或写 02；不做业务资产判断。"""

    def __init__(self, adapter: KnowledgeBaseAdapter, ocr_provider: LocalOcrProvider | None = None, *, ocr_confidence_threshold: float = 0.85) -> None:
        self.adapter = adapter
        self.ocr_provider = ocr_provider
        self.ocr_confidence_threshold = ocr_confidence_threshold
        self._names: dict[tuple[str, str], str] = {}

    def execute(self, request: IntakeRequest) -> IntakeResponse:
        digest = hashlib.sha256(request.payload).hexdigest()
        source_id = f"SRC-{digest[:24]}"
        suffix = PurePath(request.file_name).suffix.lower()
        evidence = self._evidence(request, suffix, source_id)
        ready = self._ready(request.binding, evidence)
        if ready is not None:
            return IntakeResponse("exception", ready[0], source_id, (), None, evidence)
        if request.permission_status != "allowed":
            code = "permission_denied" if request.permission_status == "denied" else "ownership_unknown"
            return self._exception(request.binding, source_id, code, evidence)
        if suffix not in SUPPORTED_SUFFIXES:
            return self._exception(request.binding, source_id, "format_unsupported", evidence)
        if request.page_evidence_mode == "required" and not request.original_retention_approved:
            evidence["page_evidence"] = {"mode": "required", "status": "approval_required"}
            return self._exception(request.binding, source_id, "privacy_approval_required", evidence)
        existing = self._reuse_existing_source(request, source_id, digest, evidence)
        if existing is not None:
            return existing
        try:
            conversion = self._readable(request.payload, suffix)
        except ConverterUnavailable:
            evidence["format_note"] = "本机缺少可运行的 MarkItDown；未保存原件或正文。"
            return self._exception(request.binding, source_id, "format_unsupported", evidence)
        except (UnicodeError, ConversionFailed):
            return self._exception(request.binding, source_id, "conversion_failed", evidence)
        except (csv.Error, ValueError):
            return self._exception(request.binding, source_id, "source_unreadable", evidence)
        body = conversion.text
        evidence["conversion"] = {"engine": conversion.engine, "version": conversion.version}
        initial_sensitive_count = _sensitive_count(body)
        if initial_sensitive_count and not request.original_retention_approved:
            evidence["privacy"] = {
                "sensitive_match_count": initial_sensitive_count,
                "original_retention_approved": False,
            }
            return self._exception(request.binding, source_id, "privacy_approval_required", evidence)
        name_key = (request.binding.client_id, request.file_name.casefold())
        prior_source = self._names.get(name_key)
        version_of = None
        if prior_source and prior_source != source_id:
            confirmed = request.confirmed_version_of == prior_source and bool(request.stable_source_locator)
            if not confirmed:
                return self._exception(request.binding, source_id, "version_conflict", evidence)
            version_of = prior_source
        rendered_pages: tuple[RenderedPage, ...] = ()
        page_engine: str | None = None
        if request.page_evidence_mode == "required":
            if suffix not in {".pdf", ".pptx"}:
                evidence["page_evidence"] = {"mode": "required", "status": "unsupported_format"}
                return self._exception(request.binding, source_id, "page_evidence_failed", evidence)
            try:
                with tempfile.TemporaryDirectory(prefix="zsk-page-evidence-") as folder:
                    rendered = render_page_evidence(request.payload, suffix, source_id, Path(folder), dpi=300)
            except PageRendererUnavailable:
                evidence["page_evidence"] = {"mode": "required", "status": "unavailable"}
                return self._exception(request.binding, source_id, "page_evidence_unavailable", evidence)
            except PageRenderFailed:
                evidence["page_evidence"] = {"mode": "required", "status": "failed"}
                return self._exception(request.binding, source_id, "page_evidence_failed", evidence)
            rendered_pages = rendered.pages
            page_engine = rendered.engine
            evidence["page_evidence"] = {
                "mode": "required", "status": "complete", "engine": page_engine,
                "page_count": rendered.page_count,
                "page_sha256": [page.artifact.sha256 for page in rendered_pages],
            }
        page_artifacts = tuple(page.artifact for page in rendered_pages)
        page_text_evidence: tuple[PageTextEvidence, ...] = ()
        if page_artifacts:
            try:
                page_text_evidence = build_page_text_evidence(
                    source_id,
                    suffix,
                    request.payload,
                    rendered_pages,
                    self.ocr_provider,
                    corrections=request.ocr_corrections,
                    confidence_threshold=self.ocr_confidence_threshold,
                )
            except OcrUnavailable:
                evidence["page_text_evidence"] = {"status": "ocr_unavailable"}
                return self._exception(request.binding, source_id, "ocr_unavailable", evidence)
            except OcrFailed:
                evidence["page_text_evidence"] = {"status": "ocr_failed"}
                return self._exception(request.binding, source_id, "ocr_failed", evidence)
            except PageTextFailed:
                evidence["page_text_evidence"] = {"status": "failed"}
                return self._exception(request.binding, source_id, "page_evidence_failed", evidence)
            unverified_pages = [item.page_number for item in page_text_evidence if item.review_status == "review_required"]
            if unverified_pages:
                evidence["page_text_evidence"] = {"status": "quality_insufficient", "page_numbers": unverified_pages}
                evidence.update({"status": "exception", "code": "file_quality_insufficient"})
                return IntakeResponse("exception", "file_quality_insufficient", source_id, (), None, evidence)
            evidence["page_text_evidence"] = {
                "status": "verified",
                "page_count": len(page_text_evidence),
                "evidence_sha256": [item.evidence_sha256 for item in page_text_evidence],
            }
        sensitive_texts = [body]
        for item in page_text_evidence:
            sensitive_texts.extend((item.native_text, item.ocr_text, item.verbatim_text))
        sensitive_count = _sensitive_count(*sensitive_texts)
        evidence["privacy"] = {
            "sensitive_match_count": sensitive_count,
            "original_retention_approved": request.original_retention_approved,
        }
        if sensitive_count and not request.original_retention_approved:
            return self._exception(request.binding, source_id, "privacy_approval_required", evidence)
        privacy_status = "redacted" if sensitive_count else "passed"
        if sensitive_count:
            body = _redact_sensitive(body)
            page_text_evidence = tuple(_redact_page_text(item) for item in page_text_evidence)
            if page_text_evidence:
                evidence["page_text_evidence"]["evidence_sha256"] = [
                    item.evidence_sha256 for item in page_text_evidence
                ]
        display_name = human_source_label(request.source_title)
        if page_text_evidence:
            body = self._with_page_text(body, page_text_evidence)
        if page_artifacts:
            body = self._with_page_evidence(body, page_artifacts, request.binding.backend_type)
        readable = self._document(
            request, source_id, digest, privacy_status, version_of, conversion, body,
            page_artifacts=page_artifacts, page_engine=page_engine, display_name=display_name,
            page_text_evidence=page_text_evidence,
        )
        record = SourceRecord(
            SOURCE_SCHEMA, source_id, request.binding.client_id, request.source_title.strip(), "unknown",
            self._content_kind(suffix), request.file_name, digest,
            hashlib.sha256(readable).hexdigest(), privacy_status, request.permission_status, version_of,
            "registered", request.original_retention_approved or not sensitive_count,
            page_evidence_mode=request.page_evidence_mode,
            page_count=len(page_artifacts),
            page_artifacts=page_artifacts,
            display_name=display_name,
            page_text_evidence=page_text_evidence,
        )
        original = self.adapter.store_original(request.binding, record, request.payload)
        self._event(evidence, "store_original", original.status, original.code)
        if original.status not in {"ok", "reused"}:
            return self._exception(request.binding, source_id, original.code or "write_failed", evidence)
        readable_result = self.adapter.store_readable(request.binding, record, readable)
        self._event(evidence, "store_readable", readable_result.status, readable_result.code)
        if readable_result.status not in {"ok", "reused"}:
            return self._exception(request.binding, source_id, readable_result.code or "write_failed", evidence, original.object_refs)
        refs = original.object_refs + readable_result.object_refs
        write_statuses = [original.status, readable_result.status]
        for rendered_page in rendered_pages:
            page_result = self.adapter.store_page_evidence(
                request.binding, record, rendered_page.artifact, rendered_page.payload
            )
            self._event(evidence, "store_page_evidence", page_result.status, page_result.code)
            if page_result.status not in {"ok", "reused"}:
                return self._exception(
                    request.binding, source_id, page_result.code or "write_failed", evidence, refs
                )
            refs += page_result.object_refs
            write_statuses.append(page_result.status)
        readback = self.adapter.read_back(request.binding, refs)
        self._event(evidence, "read_back", readback.status, readback.code)
        if readback.status not in {"ok", "reused"}:
            return self._exception(request.binding, source_id, readback.code or "readback_failed", evidence, refs)
        self._names[name_key] = source_id
        status = "reused" if all(item == "reused" for item in write_statuses) else "registered"
        evidence.update({"status": status, "code": None, "output_ref_ids": [ref.object_id for ref in refs]})
        return IntakeResponse(status, None, source_id, refs, record, evidence)

    def _reuse_existing_source(self, request: IntakeRequest, source_id: str, digest: str, evidence: dict[str, Any]) -> IntakeResponse | None:
        """新旧 Obsidian 布局均先按原件哈希复用，避免重复转换或误写 02。"""
        lookup = getattr(self.adapter, "reuse_existing_source", None)
        if not callable(lookup):
            return None
        result = lookup(
            request.binding,
            source_id,
            digest,
            request.source_role,
            request.page_evidence_mode,
            request.original_retention_approved,
        )
        if result is None:
            return None
        self._event(evidence, "reuse_existing_source", result.status, result.code)
        if result.status not in {"ok", "reused"}:
            if result.code == "binding_conflict":
                evidence.update({"status": "exception", "code": "binding_conflict", "output_ref_ids": []})
                return IntakeResponse("exception", "binding_conflict", source_id, (), None, evidence)
            return self._exception(request.binding, source_id, result.code or "readback_failed", evidence, result.object_refs)
        source = result.metadata.get("source_record")
        if not isinstance(source, SourceRecord):
            return self._exception(request.binding, source_id, "readback_failed", evidence, result.object_refs)
        readback = self.adapter.read_back(request.binding, result.object_refs)
        self._event(evidence, "read_back", readback.status, readback.code)
        if readback.status not in {"ok", "reused"}:
            return self._exception(request.binding, source_id, readback.code or "readback_failed", evidence, result.object_refs)
        self._names[(request.binding.client_id, request.file_name.casefold())] = source_id
        evidence.update({
            "status": "reused",
            "code": None,
            "existing_layout_reused": result.metadata.get("layout"),
            "output_ref_ids": [ref.object_id for ref in result.object_refs],
        })
        return IntakeResponse("reused", None, source_id, result.object_refs, source, evidence)

    def _ready(self, binding: Binding, evidence: dict[str, Any]) -> tuple[str, str] | None:
        for action, call in (("doctor", self.adapter.doctor), ("resolve_binding", lambda: self.adapter.resolve_binding(binding)), ("inspect_structure", lambda: self.adapter.inspect_structure(binding))):
            result = call()
            self._event(evidence, action, result.status, result.code)
            if result.status not in {"ok", "reused"}:
                evidence.update({"status": "exception", "code": result.code})
                return result.code or "write_failed", action
            if action == "inspect_structure" and result.status != "reused":
                evidence.update({"status": "exception", "code": "structure_conflict"})
                return "structure_conflict", action
        return None

    def _exception(self, binding: Binding, source_id: str, code: str, evidence: dict[str, Any], refs: tuple[BackendObjectRef, ...] = ()) -> IntakeResponse:
        note = _SAFE_NOTES.get(code, "资料未能安全登记，已停止后续处理。")
        question = _QUESTIONS.get(code, "请确认资料后再重试。")
        exception_id = "EXC-" + hashlib.sha256(f"{source_id}:{code}".encode()).hexdigest()[:16]
        result = self.adapter.write_exception(binding, ExceptionRecord(exception_id, source_id, code, note, question, refs))
        self._event(evidence, "write_exception", result.status, result.code)
        final_code = code if result.status in {"ok", "reused"} else result.code or "write_failed"
        output_refs = refs + result.object_refs
        evidence.update({"status": "exception", "code": final_code, "exception_id": exception_id, "output_ref_ids": [ref.object_id for ref in output_refs]})
        return IntakeResponse("exception", final_code, source_id, output_refs, None, evidence)

    @staticmethod
    def _readable(payload: bytes, suffix: str) -> MarkdownConversion:
        if suffix not in {".md", ".txt", ".csv"}:
            return convert_to_markdown(payload, suffix)
        text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip() or "\x00" in text:
            raise ValueError("empty or binary input")
        if suffix != ".csv":
            return MarkdownConversion(text.rstrip() + "\n", "zsk-text", "v1")
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        if not rows or not rows[0] or any(not cell.strip() for cell in rows[0]):
            raise ValueError("CSV requires a non-empty header")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("CSV column count is inconsistent")
        escaped = [[cell.replace("|", "\\|").replace("\n", " ") for cell in row] for row in rows]
        lines = ["| " + " | ".join(escaped[0]) + " |", "| " + " | ".join("---" for _ in escaped[0]) + " |"]
        lines.extend("| " + " | ".join(row) + " |" for row in escaped[1:])
        return MarkdownConversion("\n".join(lines) + "\n", "zsk-csv", "v1")

    @staticmethod
    def _document(
        request: IntakeRequest,
        source_id: str,
        original_sha256: str,
        privacy_status: str,
        version_of: str | None,
        conversion: MarkdownConversion,
        body: str,
        *,
        page_artifacts: tuple[PageArtifact, ...] = (),
        page_text_evidence: tuple[PageTextEvidence, ...] = (),
        page_engine: str | None = None,
        display_name: str = "",
    ) -> bytes:
        suffix = PurePath(request.file_name).suffix.lower()
        unit_kind, unit_count = Stage5Intake._content_units(suffix, body)
        fields = {
            "client_id": request.binding.client_id,
            "source_id": source_id, "source_title": request.source_title.strip(),
            "display_name": display_name,
            "original_file_name": request.file_name, "source_format": suffix.removeprefix("."),
            "source_role": "unknown",
            "original_sha256": original_sha256, "privacy_status": privacy_status,
            "permission_status": request.permission_status, "original_retention_approved": request.original_retention_approved or privacy_status == "passed",
            "version_of": version_of, "conversion_engine": conversion.engine, "conversion_version": conversion.version,
            "content_unit_kind": unit_kind, "content_unit_count": unit_count,
            "page_evidence_mode": request.page_evidence_mode,
            "page_evidence_engine": page_engine,
            "page_count": len(page_artifacts),
            "page_manifest": [item.as_dict() for item in page_artifacts],
            "page_text_manifest": [item.as_dict() for item in page_text_evidence],
        }
        frontmatter = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
        return f"---\n{frontmatter}\n---\n\n{body}".encode("utf-8")

    @staticmethod
    def _with_page_text(body: str, pages: tuple[PageTextEvidence, ...]) -> str:
        sections = ["## 页级校对正文"]
        for page in pages:
            sections.extend(
                (
                    f"### 第 {page.page_number} 页",
                    page.verbatim_text or "（本页无可核验文字）",
                    f"- 文字来源：{page.text_source}",
                    f"- 校对状态：{page.review_status}",
                    f"- 置信度：{page.confidence:.6f}",
                    f"- 证据哈希：`{page.evidence_sha256}`",
                )
            )
        return body.rstrip() + "\n\n" + "\n\n".join(sections) + "\n"

    @staticmethod
    def _with_page_evidence(body: str, pages: tuple[PageArtifact, ...], backend_type: str) -> str:
        """把真实存在的页证据写进可读版；飞书不用无法解析的本地图片链接。"""
        body = re.sub(
            r"> \[!note\] 原文图片未在轻量文字模式中保存(?:（[^\n]*）)?。",
            "> [!note] 原始页视觉已保存，请查看对应的完整页面证据。",
            body,
        )
        if backend_type == "obsidian":
            links = {page.page_number: f"![[页面证据/{page_file_name(page.page_number)}]]" for page in pages}
            used: set[int] = set()

            def insert(match: re.Match[str]) -> str:
                number = int(match.group(1))
                link = links.get(number)
                if link is None:
                    return match.group(0)
                used.add(number)
                return f"{match.group(0)}\n\n{link}"

            body = re.sub(r"(?m)^## 第\s*(\d+)\s*页\s*$", insert, body)
            missing = [links[number] for number in sorted(links) if number not in used]
            if missing:
                body = body.rstrip() + "\n\n## 完整页面证据\n\n" + "\n\n".join(missing) + "\n"
            return body
        evidence = "\n".join(f"- 第 {page.page_number} 页：附件「{page_file_name(page.page_number)}」" for page in pages)
        return body.rstrip() + "\n\n## 完整页面证据\n\n" + evidence + "\n"

    @staticmethod
    def _content_units(suffix: str, body: str) -> tuple[str | None, int | None]:
        return None, None

    @staticmethod
    def _content_kind(suffix: str) -> str:
        if suffix == ".pptx":
            return "presentation"
        if suffix in _TABLE_SUFFIXES:
            return "table"
        if suffix in {".pdf", ".docx"}:
            return "document"
        return "text"

    @staticmethod
    def _evidence(request: IntakeRequest, suffix: str, source_id: str) -> dict[str, Any]:
        return {
            "schema_version": "zsk-stage5-intake-evidence-v1", "task_id": request.task_id, "phase_id": "ZSK-P5",
            "safe_input_summary": f"stage5:{suffix.removeprefix('.') or 'none'}:neutral", "source_id": source_id,
            "events": [], "privacy": {"sensitive_match_count": 0, "original_retention_approved": request.original_retention_approved},
            "model_call_count": 0, "downstream_asset_call_count": 0,
            "page_evidence": {"mode": request.page_evidence_mode, "status": "not_requested"},
        }

    @staticmethod
    def _event(evidence: dict[str, Any], action: str, status: str, code: str | None) -> None:
        evidence["events"].append({"action": action, "status": status, "code": code})
