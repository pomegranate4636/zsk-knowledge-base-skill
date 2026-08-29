"""Single programmatic entry for bootstrap, binding, intake and publishing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .approval import ApprovalIO, ApprovalStore, FileApprovalIO
from .content_workflow import ContentWorkflow
from .contracts import AdapterResult, Binding, ROOT_KEYS
from .feishu_adapter import FeishuAdapter
from .feishu_cli import CliRunner, SubprocessCliRunner
from .feishu_publish import (
    FeishuAdapterPublishBackend,
    FeishuPublishRequest,
    FeishuPublishWorkflow,
    PublishReceiptStore,
    PublishResult,
)
from .obsidian_adapter import ObsidianAdapter
from .runtime_state import (
    BindingStore,
    BootstrapConfirmationStore,
    ExistingBindingResult,
    ExistingBindingService,
    ReadinessStore,
)
from .stage11_bootstrap import FirstRunBootstrap
from .stage5_intake import IntakeRequest, IntakeResponse, Stage5Intake


@dataclass(frozen=True)
class PreparedActive:
    status: str
    code: str | None
    binding: Binding | None
    adapter: object | None


class ZskEntry:
    """The only runtime facade public Router code needs to instantiate."""

    def __init__(
        self,
        runtime_root: Path,
        *,
        io: ApprovalIO | None = None,
        runner: CliRunner | None = None,
        adapter_factory: Callable[[Binding], object] | None = None,
    ) -> None:
        self.runtime_root = runtime_root
        self.io = io or FileApprovalIO()
        self.runner = runner or SubprocessCliRunner()
        self.bindings = BindingStore(runtime_root, io=self.io)
        self.readiness = ReadinessStore(runtime_root, io=self.io)
        self.confirmations = BootstrapConfirmationStore(runtime_root, io=self.io)
        self.approvals = ApprovalStore(runtime_root / "content-approvals", io=self.io)
        self.publish_receipts = PublishReceiptStore(runtime_root / "publish-receipts", io=self.io)
        self.adapter_factory = adapter_factory or self._default_adapter
        self._adapters: dict[str, object] = {}

    def first_run(self, **kwargs) -> FirstRunBootstrap:
        return FirstRunBootstrap(
            runner=self.runner,
            runtime_root=self.runtime_root,
            binding_store=self.bindings,
            readiness_store=self.readiness,
            confirmation_store=self.confirmations,
            **kwargs,
        )

    def bind_existing(self, binding: Binding) -> ExistingBindingResult:
        adapter = self._adapter(binding)
        return ExistingBindingService(self.bindings, self.readiness).bind(binding, adapter)

    def active_binding(self, *, required: bool = True) -> Binding | None:
        return self.bindings.load_active(required=required)

    def prepare_active(self) -> PreparedActive:
        binding = self.active_binding(required=False)
        if binding is None:
            return PreparedActive("blocked", "binding_missing", None, None)
        adapter = self._adapter(binding)
        if not self.readiness.is_ready(binding):
            result = ExistingBindingService(self.bindings, self.readiness).bind(binding, adapter)
            return PreparedActive("ready" if result.status == "bound" else "blocked", result.code, result.binding, adapter if result.status == "bound" else None)
        resolved = adapter.resolve_binding(binding)
        if resolved.status not in {"ok", "reused"}:
            self._invalidate_if_remote_auth(resolved)
            return PreparedActive("blocked", resolved.code or "binding_missing", binding, None)
        structure = adapter.inspect_structure(binding)
        if structure.status != "reused" or len(structure.object_refs) != len(ROOT_KEYS):
            self._invalidate_if_remote_auth(structure)
            return PreparedActive("blocked", structure.code or "structure_conflict", binding, None)
        rules = adapter.read_rules(binding)
        if rules.status not in {"ok", "reused"}:
            self._invalidate_if_remote_auth(rules)
            return PreparedActive("blocked", rules.code or "readback_failed", binding, None)
        return PreparedActive("ready_cached", None, binding, adapter)

    def ingest(self, request: IntakeRequest) -> IntakeResponse:
        prepared = self.prepare_active()
        if prepared.adapter is None or prepared.binding is None:
            raise ValueError(prepared.code or "binding_missing")
        if request.binding != prepared.binding:
            raise ValueError("binding_conflict")
        return Stage5Intake(prepared.adapter).execute(request)

    def content_workflow(self) -> ContentWorkflow:
        return ContentWorkflow(self.approvals)

    def publish_feishu(self, request: FeishuPublishRequest) -> PublishResult:
        prepared = self.prepare_active()
        if prepared.adapter is None or prepared.binding is None:
            raise ValueError(prepared.code or "binding_missing")
        if request.binding != prepared.binding or prepared.binding.backend_type != "feishu":
            raise ValueError("binding_conflict")
        backend = FeishuAdapterPublishBackend(prepared.adapter)
        result = FeishuPublishWorkflow(backend, self.publish_receipts).publish(request)
        if result.record.get("last_error") in {"feishu_auth_missing", "permission_denied"}:
            self.readiness.invalidate()
        return result

    def _adapter(self, binding: Binding):
        key = f"{binding.client_id}:{binding.backend_type}:{binding.backend_locator}"
        if key not in self._adapters:
            self._adapters[key] = self.adapter_factory(binding)
        return self._adapters[key]

    def _default_adapter(self, binding: Binding):
        if binding.backend_type == "feishu":
            return FeishuAdapter(self.runner)
        if binding.backend_type == "obsidian":
            return ObsidianAdapter()
        raise ValueError("backend_unsupported")

    def _invalidate_if_remote_auth(self, result: AdapterResult) -> None:
        if result.code in {"feishu_auth_missing", "permission_denied"}:
            self.readiness.invalidate()
