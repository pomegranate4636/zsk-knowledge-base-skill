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
    PrivacyDecision,
    RouteDecision,
    SourceRecord,
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
    "ExceptionRecord",
    "EvidenceRecorder",
    "FakeAdapter",
    "FakeFaults",
    "KnowledgeBaseAdapter",
    "RouteDecision",
    "PrivacyDecision",
    "RunEvidence",
    "SourceRecord",
]
