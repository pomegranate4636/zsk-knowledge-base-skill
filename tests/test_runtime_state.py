from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import (  # noqa: E402
    BINDING_SCHEMA,
    ROOT_KEYS,
    AdapterResult,
    BackendObjectRef,
    Binding,
)
from shared.runtime_state import (  # noqa: E402
    BindingStore,
    BootstrapConfirmationStore,
    ExistingBindingService,
    ReadinessStore,
)
from shared.templates import TEMPLATE_VERSION  # noqa: E402


class MemoryIO:
    def __init__(self) -> None:
        self.data = {}

    def read(self, path):
        return self.data.get(str(path))

    def write_atomic(self, path, payload):
        self.data[str(path)] = payload


def binding(locator: str = "https://feishu.cn/wiki/space/123") -> Binding:
    return Binding(
        BINDING_SCHEMA, "CLT-RUNTIME", "沈牧", "豪宅知识库", "person", "feishu", locator,
        {key: f"root:{key}" for key in ROOT_KEYS}, TEMPLATE_VERSION,
    )


class ReadyAdapter:
    def __init__(self, *, doctor_result: AdapterResult | None = None) -> None:
        self.calls = []
        self.doctor_result = doctor_result or AdapterResult.ok()

    def doctor(self):
        self.calls.append("doctor")
        return self.doctor_result

    def resolve_binding(self, _binding):
        self.calls.append("resolve")
        return AdapterResult.ok()

    def inspect_structure(self, _binding):
        self.calls.append("inspect")
        refs = tuple(BackendObjectRef(f"root-{key}", "root", f"opaque://{key}") for key in ROOT_KEYS)
        return AdapterResult.reused(*refs)

    def read_rules(self, _binding):
        self.calls.append("rules")
        return AdapterResult.ok()


class RuntimeStateTests(unittest.TestCase):
    def test_binding_round_trip_is_persistent_and_contains_no_credentials(self) -> None:
        io = MemoryIO()
        store = BindingStore(Path("C:/runtime"), io=io)
        store.save_active(binding())

        loaded = BindingStore(Path("C:/runtime"), io=io).load_active()

        self.assertEqual(loaded, binding())
        raw = next(iter(io.data.values())).decode("utf-8")
        self.assertNotIn("token", raw.lower())
        self.assertNotIn("device_code", raw)

    def test_existing_binding_is_saved_only_after_remote_structure_and_rules_readback(self) -> None:
        io = MemoryIO()
        bindings = BindingStore(Path("C:/runtime"), io=io)
        readiness = ReadinessStore(Path("C:/runtime"), io=io)
        adapter = ReadyAdapter()

        result = ExistingBindingService(bindings, readiness).bind(binding(), adapter)

        self.assertEqual(result.status, "bound")
        self.assertEqual(adapter.calls, ["doctor", "resolve", "inspect", "rules"])
        self.assertEqual(bindings.load_active(), binding())
        self.assertTrue(readiness.is_ready(binding()))

    def test_incomplete_existing_structure_is_not_saved(self) -> None:
        class PartialAdapter(ReadyAdapter):
            def inspect_structure(self, _binding):
                self.calls.append("inspect")
                return AdapterResult.ok(metadata={"structure_state": "partial"})

        io = MemoryIO()
        bindings = BindingStore(Path("C:/runtime"), io=io)
        result = ExistingBindingService(bindings, ReadinessStore(Path("C:/runtime"), io=io)).bind(binding(), PartialAdapter())

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.code, "structure_conflict")
        self.assertIsNone(bindings.load_active(required=False))

    def test_readiness_fingerprint_changes_with_binding_target(self) -> None:
        io = MemoryIO()
        readiness = ReadinessStore(Path("C:/runtime"), io=io)
        readiness.mark_ready(binding())
        self.assertTrue(readiness.is_ready(binding()))
        self.assertFalse(readiness.is_ready(binding("https://feishu.cn/wiki/space/456")))

    def test_bootstrap_confirmation_survives_new_store_instance_and_is_one_time(self) -> None:
        io = MemoryIO()
        issued = BootstrapConfirmationStore(Path("C:/runtime"), io=io, now=lambda: 100)
        token = issued.issue({"backend": "feishu", "name": "豪宅知识库", "target": "飞书"})

        resumed = BootstrapConfirmationStore(Path("C:/runtime"), io=io, now=lambda: 101)
        self.assertTrue(resumed.consume(token, {"backend": "feishu", "name": "豪宅知识库", "target": "飞书"}))
        self.assertFalse(resumed.consume(token, {"backend": "feishu", "name": "豪宅知识库", "target": "飞书"}))


if __name__ == "__main__":
    unittest.main()
