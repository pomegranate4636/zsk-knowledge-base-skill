"""后端中立的 ZSK 阶段 1 数据合同。

本模块只保存稳定身份、状态和不透明对象引用；不保存凭据、客户正文或任何后端私有字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit


BINDING_SCHEMA = "zsk-client-binding-v1"
REGISTRY_SCHEMA = "zsk-registry-v1"
SOURCE_SCHEMA = "zsk-source-record-v1"
PRIVACY_SCHEMA = "zsk-privacy-v1"
EVIDENCE_SCHEMA = "zsk-run-evidence-v1"
ROOT_KEYS = ("01", "02", "03", "04", "05", "06", "07", "AGENTS", "README")
ADAPTER_METHODS = (
    "doctor",
    "resolve_binding",
    "inspect_structure",
    "create_skeleton",
    "read_rules",
    "store_original",
    "store_readable",
    "store_page_evidence",
    "write_exception",
    "write_knowledge_asset",
    "write_method_asset",
    "write_profile",
    "read_back",
)
ERROR_CODES = frozenset(
    {
        "binding_missing",
        "binding_conflict",
        "backend_unsupported",
        "dependency_missing",
        "feishu_cli_missing",
        "feishu_auth_missing",
        "permission_denied",
        "structure_conflict",
        "source_unreadable",
        "format_unsupported",
        "conversion_failed",
        "page_evidence_unavailable",
        "page_evidence_failed",
        "ocr_unavailable",
        "ocr_failed",
        "ocr_review_required",
        "evidence_incomplete",
        "privacy_blocked",
        "privacy_approval_required",
        "ownership_unknown",
        "version_conflict",
        "duplicate_conflict",
        "routing_ambiguous",
        "profile_identity_mismatch",
        "write_failed",
        "readback_failed",
        "confirmation_mismatch",
        "receipt_expired",
        "receipt_reused",
        "credential_locator",
    }
)
SOURCE_ROLES = frozenset({"business_knowledge", "reference_method", "profile_material", "mixed", "unknown"})
CONTENT_KINDS = frozenset({"text", "document", "presentation", "table", "image", "mixed"})
PRIVACY_STATES = frozenset({"passed", "redacted", "approval_required", "blocked"})
PERMISSION_STATES = frozenset({"allowed", "unknown", "denied"})
SOURCE_STATUSES = frozenset({"registered", "reused", "indexed_only", "exception"})
ROUTES = frozenset({"03", "04", "05", "indexed_only", "02"})
SUBJECT_TYPES = frozenset({"person", "company", "project"})
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SOURCE_ID = re.compile(r"^SRC-(?:[0-9]{8}-[0-9]{3}|[0-9a-f]{24})$")
CLIENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
TASK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FAKE_RUN_ID = re.compile(r"^ZSK-S[12]-FAKE-[0-9]{8}-[0-9]{3}$")
SOURCE_ARTIFACT_KINDS = frozenset({"original", "readable"})


class ContractError(ValueError):
    """输入不满足共享合同。"""

    def __init__(self, code: str, detail: str) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown contract error code: {code}")
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _non_empty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError("binding_missing", f"{field_name} must be a non-empty string")
    return value.strip()


LOCATOR_CREDENTIAL_KEYS = frozenset({"token", "cookie", "password", "access_token", "secret", "api_key", "apikey", "authorization", "credential", "session"})


def locator_has_credential(locator: str) -> bool:
    parsed = urlsplit(locator)
    pairs = (*parse_qsl(parsed.query, keep_blank_values=True), *parse_qsl(parsed.fragment, keep_blank_values=True))
    return any(key.lower().replace("-", "_") in LOCATOR_CREDENTIAL_KEYS for key, _value in pairs)


@dataclass(frozen=True)
class BackendObjectRef:
    """跨后端稳定引用；locator 对上层是不透明字符串。"""

    object_id: str
    object_kind: str
    locator: str
    version: str = "1"

    def __post_init__(self) -> None:
        _non_empty(self.object_id, "object_id")
        _non_empty(self.object_kind, "object_kind")
        _non_empty(self.locator, "locator")
        _non_empty(self.version, "version")

    def as_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "object_kind": self.object_kind,
            "locator": self.locator,
            "version": self.version,
        }


@dataclass(frozen=True)
class PageArtifact:
    """一个已登记来源的完整页面证据。"""

    page_id: str
    source_id: str
    page_number: int
    file_name: str
    sha256: str
    width_px: int = 0
    height_px: int = 0

    def __post_init__(self) -> None:
        _non_empty(self.page_id, "page_id")
        if not SOURCE_ID.fullmatch(self.source_id):
            raise ContractError("source_unreadable", "page source_id is invalid")
        if self.page_number < 1:
            raise ContractError("source_unreadable", "page_number must be positive")
        if self.page_id != f"{self.source_id}-PAGE-{self.page_number:03d}":
            raise ContractError("source_unreadable", "page_id must match source and page number")
        if self.file_name != f"page-{self.page_number:03d}.png":
            raise ContractError("source_unreadable", "page file name must match its page number")
        if not HEX64.fullmatch(self.sha256):
            raise ContractError("source_unreadable", "page sha256 must be a SHA256")
        if self.width_px < 0 or self.height_px < 0:
            raise ContractError("source_unreadable", "page dimensions cannot be negative")
        if bool(self.width_px) != bool(self.height_px):
            raise ContractError("source_unreadable", "page dimensions must be both present or both absent")

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "source_id": self.source_id,
            "page_number": self.page_number,
            "file_name": self.file_name,
            "sha256": self.sha256,
            "width_px": self.width_px,
            "height_px": self.height_px,
        }


@dataclass(frozen=True)
class PageTextEvidence:
    """页图、原生文字、OCR 与校对结果组成的内容寻址证据。"""

    source_id: str
    page_number: int
    page_sha256: str
    native_text: str
    ocr_text: str
    verbatim_text: str
    text_source: str
    confidence: float
    review_status: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not SOURCE_ID.fullmatch(self.source_id) or self.page_number < 1:
            raise ContractError("source_unreadable", "page text identity is invalid")
        if not HEX64.fullmatch(self.page_sha256) or not HEX64.fullmatch(self.evidence_sha256):
            raise ContractError("source_unreadable", "page text hashes must be SHA256")
        if self.text_source not in {"native", "ocr", "native+ocr", "none"}:
            raise ContractError("source_unreadable", "page text source is invalid")
        if self.review_status not in {"verified_native", "auto_verified", "approved", "review_required"}:
            raise ContractError("source_unreadable", "page text review status is invalid")
        if not 0.0 <= self.confidence <= 1.0:
            raise ContractError("source_unreadable", "page text confidence must be between zero and one")
        expected = self._digest(
            self.source_id,
            self.page_number,
            self.page_sha256,
            self.native_text,
            self.ocr_text,
            self.verbatim_text,
            self.text_source,
            self.confidence,
            self.review_status,
        )
        if self.evidence_sha256 != expected:
            raise ContractError("source_unreadable", "page text evidence hash does not match its content")
        if self.review_status == "review_required" and self.verbatim_text:
            raise ContractError("ocr_review_required", "unreviewed OCR cannot expose verified verbatim text")

    @classmethod
    def create(
        cls,
        source_id: str,
        page_number: int,
        page_sha256: str,
        *,
        native_text: str,
        ocr_text: str,
        verbatim_text: str,
        text_source: str,
        confidence: float,
        review_status: str,
    ) -> "PageTextEvidence":
        confidence = round(float(confidence), 6)
        digest = cls._digest(
            source_id,
            page_number,
            page_sha256,
            native_text,
            ocr_text,
            verbatim_text,
            text_source,
            confidence,
            review_status,
        )
        return cls(
            source_id,
            page_number,
            page_sha256,
            native_text,
            ocr_text,
            verbatim_text,
            text_source,
            confidence,
            review_status,
            digest,
        )

    @staticmethod
    def _digest(
        source_id: str,
        page_number: int,
        page_sha256: str,
        native_text: str,
        ocr_text: str,
        verbatim_text: str,
        text_source: str,
        confidence: float,
        review_status: str,
    ) -> str:
        import json

        material = {
            "source_id": source_id,
            "page_number": page_number,
            "page_sha256": page_sha256,
            "native_text": native_text,
            "ocr_text": ocr_text,
            "verbatim_text": verbatim_text,
            "text_source": text_source,
            "confidence": confidence,
            "review_status": review_status,
        }
        canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "page_number": self.page_number,
            "page_sha256": self.page_sha256,
            "native_text": self.native_text,
            "ocr_text": self.ocr_text,
            "verbatim_text": self.verbatim_text,
            "text_source": self.text_source,
            "confidence": self.confidence,
            "review_status": self.review_status,
            "evidence_sha256": self.evidence_sha256,
        }


@dataclass(frozen=True)
class KnowledgeFact:
    """知识卡中的一条事实及其逐页原文证据。"""

    fact: str
    page_number: int
    verbatim_text: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        _non_empty(self.fact, "fact")
        _non_empty(self.verbatim_text, "verbatim_text")
        if self.page_number < 1 or not HEX64.fullmatch(self.evidence_sha256):
            raise ContractError("evidence_incomplete", "knowledge fact page or evidence hash is invalid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
            "page_number": self.page_number,
            "verbatim_text": self.verbatim_text,
            "evidence_sha256": self.evidence_sha256,
        }


@dataclass(frozen=True)
class AdapterResult:
    status: str
    code: str | None = None
    object_refs: tuple[BackendObjectRef, ...] = ()
    checked: tuple[str, ...] = ()
    detail: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"ok", "reused", "blocked", "failed"}:
            raise ValueError(f"unsupported adapter status: {self.status}")
        if self.code is not None and self.code not in ERROR_CODES:
            raise ValueError(f"unsupported error code: {self.code}")
        if self.status in {"blocked", "failed"} and self.code is None:
            raise ValueError("blocked/failed adapter results require an error code")
        if self.status in {"ok", "reused"} and self.code is not None:
            raise ValueError("successful adapter results cannot carry an error code")

    @classmethod
    def ok(cls, *refs: BackendObjectRef, checked: tuple[str, ...] = (), detail: str | None = None, metadata: Mapping[str, Any] | None = None) -> "AdapterResult":
        return cls("ok", object_refs=refs, checked=checked, detail=detail, metadata=metadata or {})

    @classmethod
    def reused(cls, *refs: BackendObjectRef, checked: tuple[str, ...] = (), detail: str | None = None, metadata: Mapping[str, Any] | None = None) -> "AdapterResult":
        return cls("reused", object_refs=refs, checked=checked, detail=detail, metadata=metadata or {})

    @classmethod
    def failed(cls, code: str, detail: str, *, blocked: bool = False, checked: tuple[str, ...] = ()) -> "AdapterResult":
        return cls("blocked" if blocked else "failed", code=code, detail=detail, checked=checked)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "object_refs": [ref.as_dict() for ref in self.object_refs],
            "checked": list(self.checked),
            "detail": self.detail,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class Binding:
    schema_version: str
    client_id: str
    client_name: str
    knowledge_base_name: str
    subject_type: str
    backend_type: str
    backend_locator: str
    root_map: Mapping[str, str]
    template_version: str
    status: str = "active"

    def __post_init__(self) -> None:
        validate_binding(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "client_id": self.client_id,
            "client_name": self.client_name,
            "knowledge_base_name": self.knowledge_base_name,
            "subject_type": self.subject_type,
            "backend_type": self.backend_type,
            "backend_locator": self.backend_locator,
            "root_map": dict(self.root_map),
            "template_version": self.template_version,
            "status": self.status,
        }


def validate_binding(binding: Binding) -> None:
    if binding.schema_version != BINDING_SCHEMA:
        raise ContractError("binding_missing", "unsupported binding schema")
    if not CLIENT_ID.fullmatch(binding.client_id):
        raise ContractError("binding_missing", "client_id is not a stable machine identifier")
    for field_name in ("client_name", "knowledge_base_name", "backend_type", "backend_locator", "template_version"):
        _non_empty(getattr(binding, field_name), field_name)
    if locator_has_credential(binding.backend_locator):
        raise ContractError("credential_locator", "backend_locator query or fragment must not contain credentials")
    if binding.subject_type not in SUBJECT_TYPES:
        raise ContractError("binding_missing", "subject_type is not supported")
    if binding.status not in {"active", "disabled"}:
        raise ContractError("binding_missing", "binding status is invalid")
    if set(binding.root_map) != set(ROOT_KEYS):
        raise ContractError("binding_missing", "root_map must cover the nine logical root objects")
    if any(not isinstance(value, str) or not value.strip() for value in binding.root_map.values()):
        raise ContractError("binding_missing", "root_map values must be stable object references")


@dataclass(frozen=True)
class RegistryOutcome:
    status: str
    code: str | None = None
    binding: Binding | None = None


class BindingRegistry:
    """只在当前进程保存绑定，阶段 1 不接触真实 Registry。"""

    def __init__(self) -> None:
        self._by_client: dict[str, Binding] = {}

    def register(self, binding: Binding) -> RegistryOutcome:
        existing = self._by_client.get(binding.client_id)
        if existing is not None:
            if existing.as_dict() == binding.as_dict():
                return RegistryOutcome("reused", binding=existing)
            if _binding_identity(existing) == _binding_identity(binding):
                self._by_client[binding.client_id] = binding
                return RegistryOutcome("updated", binding=binding)
            return RegistryOutcome("conflict", code="binding_conflict", binding=existing)
        for other in self._by_client.values():
            if other.backend_locator == binding.backend_locator and other.client_id != binding.client_id:
                return RegistryOutcome("conflict", code="binding_conflict", binding=other)
        self._by_client[binding.client_id] = binding
        return RegistryOutcome("registered", binding=binding)

    def resolve(self, client_id: str) -> Binding | None:
        return self._by_client.get(client_id)

    def snapshot(self) -> tuple[Binding, ...]:
        return tuple(self._by_client[key] for key in sorted(self._by_client))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REGISTRY_SCHEMA,
            "bindings": [binding.as_dict() for binding in self.snapshot()],
        }


def _binding_identity(binding: Binding) -> tuple[str, str, str, str, tuple[tuple[str, str], ...], str]:
    return (
        binding.client_id,
        binding.subject_type,
        binding.backend_type,
        binding.backend_locator,
        tuple(sorted(binding.root_map.items())),
        binding.template_version,
    )


@dataclass(frozen=True)
class SourceRecord:
    schema_version: str
    source_id: str
    client_id: str
    source_title: str
    source_role: str
    content_kind: str
    original_name: str
    original_sha256: str
    readable_sha256: str
    privacy_status: str
    permission_status: str
    version_of: str | None
    status: str
    original_retention_approved: bool = False
    backend_artifacts: Mapping[str, BackendObjectRef] = field(default_factory=dict)
    page_evidence_mode: str = "off"
    page_count: int = 0
    page_artifacts: tuple[PageArtifact, ...] = ()
    display_name: str = ""
    page_text_evidence: tuple[PageTextEvidence, ...] = ()

    def __post_init__(self) -> None:
        validate_source(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "client_id": self.client_id,
            "source_title": self.source_title,
            "source_role": self.source_role,
            "content_kind": self.content_kind,
            "original_name": self.original_name,
            "original_sha256": self.original_sha256,
            "readable_sha256": self.readable_sha256,
            "privacy_status": self.privacy_status,
            "permission_status": self.permission_status,
            "version_of": self.version_of,
            "status": self.status,
            "original_retention_approved": self.original_retention_approved,
            "backend_artifacts": {key: ref.as_dict() for key, ref in self.backend_artifacts.items()},
            "page_evidence_mode": self.page_evidence_mode,
            "page_count": self.page_count,
            "page_artifacts": [item.as_dict() for item in self.page_artifacts],
            "display_name": self.display_name,
            "page_text_evidence": [item.as_dict() for item in self.page_text_evidence],
        }


def validate_source(source: SourceRecord) -> None:
    if source.schema_version != SOURCE_SCHEMA:
        raise ContractError("source_unreadable", "unsupported source schema")
    if not SOURCE_ID.fullmatch(source.source_id):
        raise ContractError("source_unreadable", "source_id is invalid")
    if not CLIENT_ID.fullmatch(source.client_id):
        raise ContractError("source_unreadable", "source client_id is invalid")
    for field_name in ("source_title", "original_name"):
        _non_empty(getattr(source, field_name), field_name)
    if source.display_name:
        _non_empty(source.display_name, "display_name")
    if source.source_role not in SOURCE_ROLES:
        raise ContractError("routing_ambiguous", "source_role is not supported")
    if source.content_kind not in CONTENT_KINDS:
        raise ContractError("format_unsupported", "content_kind is not supported")
    for field_name in ("original_sha256", "readable_sha256"):
        if not HEX64.fullmatch(getattr(source, field_name)):
            raise ContractError("source_unreadable", f"{field_name} must be a SHA256")
    if source.privacy_status not in PRIVACY_STATES:
        raise ContractError("privacy_blocked", "privacy_status is invalid")
    if source.permission_status not in PERMISSION_STATES:
        raise ContractError("permission_denied", "permission_status is invalid")
    if source.version_of is not None and not SOURCE_ID.fullmatch(source.version_of):
        raise ContractError("version_conflict", "version_of is invalid")
    if source.status not in SOURCE_STATUSES:
        raise ContractError("source_unreadable", "source status is invalid")
    if not isinstance(source.original_retention_approved, bool):
        raise ContractError("privacy_blocked", "original_retention_approved must be boolean")
    if source.privacy_status == "redacted" and not source.original_retention_approved:
        raise ContractError("privacy_approval_required", "redacted sources require original retention approval")
    if set(source.backend_artifacts) - SOURCE_ARTIFACT_KINDS:
        raise ContractError("source_unreadable", "backend_artifacts contains an unsupported key")
    if any(not isinstance(ref, BackendObjectRef) for ref in source.backend_artifacts.values()):
        raise ContractError("source_unreadable", "backend_artifacts values must be object references")
    if source.page_evidence_mode not in {"off", "required"}:
        raise ContractError("source_unreadable", "page_evidence_mode is invalid")
    if any(not isinstance(item, PageArtifact) or item.source_id != source.source_id for item in source.page_artifacts):
        raise ContractError("source_unreadable", "page artifacts must belong to the source")
    pages = [item.page_number for item in source.page_artifacts]
    if any(
        not isinstance(item, PageTextEvidence) or item.source_id != source.source_id
        for item in source.page_text_evidence
    ):
        raise ContractError("source_unreadable", "page text evidence must belong to the source")
    text_pages = [item.page_number for item in source.page_text_evidence]
    if source.page_evidence_mode == "off":
        if source.page_count != 0 or source.page_artifacts:
            raise ContractError("source_unreadable", "page evidence must be empty when disabled")
        if source.page_text_evidence:
            raise ContractError("source_unreadable", "page text evidence must be empty when page mode is disabled")
    elif source.page_count < 1 or pages != list(range(1, source.page_count + 1)):
        raise ContractError("page_evidence_failed", "page evidence must be complete, ordered, and contiguous")
    elif source.page_text_evidence:
        if text_pages != pages:
            raise ContractError("evidence_incomplete", "page text evidence must be complete, ordered, and contiguous")
        by_page = {item.page_number: item for item in source.page_artifacts}
        if any(item.page_sha256 != by_page[item.page_number].sha256 for item in source.page_text_evidence):
            raise ContractError("evidence_incomplete", "page text evidence must bind the rendered page hash")


@dataclass(frozen=True)
class PrivacyDecision:
    schema_version: str
    privacy_status: str
    permission_status: str
    safe_note: str
    original_retention_approved: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != PRIVACY_SCHEMA:
            raise ContractError("privacy_blocked", "unsupported privacy schema")
        if self.privacy_status not in PRIVACY_STATES:
            raise ContractError("privacy_blocked", "privacy_status is invalid")
        if self.permission_status not in PERMISSION_STATES:
            raise ContractError("permission_denied", "permission_status is invalid")
        _non_empty(self.safe_note, "safe_note")
        if not isinstance(self.original_retention_approved, bool):
            raise ContractError("privacy_blocked", "original_retention_approved must be boolean")

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "schema_version": self.schema_version,
            "privacy_status": self.privacy_status,
            "permission_status": self.permission_status,
            "safe_note": self.safe_note,
            "original_retention_approved": self.original_retention_approved,
        }


@dataclass(frozen=True)
class RouteDecision:
    source_id: str
    route: str
    reason: str
    reason_code: str | None = None
    fragment_id: str | None = None

    def __post_init__(self) -> None:
        if not SOURCE_ID.fullmatch(self.source_id):
            raise ContractError("routing_ambiguous", "route decision source_id is invalid")
        if self.route not in ROUTES:
            raise ContractError("routing_ambiguous", "route is invalid")
        _non_empty(self.reason, "reason")
        if self.reason_code is not None and self.reason_code not in ERROR_CODES:
            raise ContractError("routing_ambiguous", "reason_code is invalid")

    def as_dict(self) -> dict[str, str | None]:
        return {
            "source_id": self.source_id,
            "route": self.route,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "fragment_id": self.fragment_id,
        }


@dataclass(frozen=True)
class AssetPayload:
    asset_id: str
    title: str
    body: str
    source_id: str
    source_role: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _non_empty(self.asset_id, "asset_id")
        _non_empty(self.title, "title")
        _non_empty(self.body, "body")
        if not SOURCE_ID.fullmatch(self.source_id):
            raise ContractError("source_unreadable", "asset source_id is invalid")
        if self.source_role not in SOURCE_ROLES:
            raise ContractError("routing_ambiguous", "asset source_role is invalid")

    def fingerprint(self) -> str:
        material = "\n".join((self.asset_id, self.title, self.body, self.source_id, self.source_role)).encode("utf-8")
        return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class ExceptionRecord:
    exception_id: str
    source_id: str
    reason_code: str
    safe_note: str
    question: str
    source_refs: tuple[BackendObjectRef, ...] = ()

    def __post_init__(self) -> None:
        _non_empty(self.exception_id, "exception_id")
        if not SOURCE_ID.fullmatch(self.source_id):
            raise ContractError("source_unreadable", "exception source_id is invalid")
        if self.reason_code not in ERROR_CODES:
            raise ContractError("routing_ambiguous", "exception reason_code is invalid")
        _non_empty(self.safe_note, "safe_note")
        _non_empty(self.question, "question")
        if any(not isinstance(ref, BackendObjectRef) for ref in self.source_refs):
            raise ContractError("source_unreadable", "exception source_refs must be object references")

    def as_dict(self) -> dict[str, Any]:
        return {
            "exception_id": self.exception_id,
            "source_id": self.source_id,
            "reason_code": self.reason_code,
            "safe_note": self.safe_note,
            "question": self.question,
            "source_refs": [ref.as_dict() for ref in self.source_refs],
        }

    def fingerprint(self) -> str:
        material = "\n".join((self.exception_id, self.source_id, self.reason_code, self.safe_note, self.question)).encode("utf-8")
        return hashlib.sha256(material).hexdigest()


def payload_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
