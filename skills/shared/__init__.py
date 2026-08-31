"""ZSK 阶段 1 的后端中立合同与 Fake Adapter。"""

from .adapter import KnowledgeBaseAdapter
from .contracts import (
    ADAPTER_METHODS,
    ERROR_CODES,
    AdapterResult,
    AssetPayload,
    BackendObjectRef,
    Binding,
    BindingRegistry,
    ExceptionRecord,
    PageArtifact,
    PrivacyDecision,
    RouteDecision,
    SourceRecord,
)
from .content_slim_handoff import (
    ContentSlimHandoffError,
    ContentSlimHandoffPlan,
    configure_content_slim_handoff,
    plan_content_slim_handoff,
)
from .evidence import EvidenceRecorder, RunEvidence
from .fake_adapter import FakeAdapter, FakeFaults

__all__ = [
    "ADAPTER_METHODS",
    "ERROR_CODES",
    "AdapterResult",
    "AssetPayload",
    "BackendObjectRef",
    "Binding",
    "BindingRegistry",
    "ContentSlimHandoffError",
    "ContentSlimHandoffPlan",
    "ExceptionRecord",
    "EvidenceRecorder",
    "FakeAdapter",
    "FakeFaults",
    "KnowledgeBaseAdapter",
    "PageArtifact",
    "RouteDecision",
    "PrivacyDecision",
    "RunEvidence",
    "SourceRecord",
    "configure_content_slim_handoff",
    "plan_content_slim_handoff",
]
