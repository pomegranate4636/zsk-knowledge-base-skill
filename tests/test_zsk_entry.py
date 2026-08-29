from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, AdapterResult, BackendObjectRef, Binding  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402
from shared.zsk_entry import ZskEntry  # noqa: E402


class MemoryIO:
    def __init__(self) -> None:
        self.data = {}

    def read(self, path):
        return self.data.get(str(path))

    def write_atomic(self, path, payload):
        self.data[str(path)] = payload


def binding(locator: str = "fake://main") -> Binding:
    return Binding(
        BINDING_SCHEMA, "CLT-ENTRY", "客户", "主知识库", "company", "obsidian", locator,
        {key: f"root:{key}" for key in ROOT_KEYS}, TEMPLATE_VERSION,
    )


class EntryAdapter:
    def __init__(self) -> None:
        self.calls = []

    def doctor(self):
        self.calls.append("doctor")
        return AdapterResult.ok()

    def resolve_binding(self, _binding):
        self.calls.append("resolve")
        return AdapterResult.ok()

    def inspect_structure(self, _binding):
        self.calls.append("inspect")
        return AdapterResult.reused(*(BackendObjectRef(f"root-{key}", "root", f"opaque://{key}") for key in ROOT_KEYS))

    def read_rules(self, _binding):
        self.calls.append("rules")
        return AdapterResult.ok()


class ZskEntryTests(unittest.TestCase):
    def test_existing_binding_becomes_the_single_active_runtime_target(self) -> None:
        io = MemoryIO()
        adapter = EntryAdapter()
        entry = ZskEntry(Path("C:/runtime"), io=io, adapter_factory=lambda _binding: adapter)

        result = entry.bind_existing(binding())

        self.assertEqual(result.status, "bound")
        self.assertEqual(entry.active_binding(), binding())
        self.assertEqual(adapter.calls, ["doctor", "resolve", "inspect", "rules"])

    def test_new_entry_reuses_ready_state_but_still_resolves_remote_structure(self) -> None:
        io = MemoryIO()
        first_adapter = EntryAdapter()
        ZskEntry(Path("C:/runtime"), io=io, adapter_factory=lambda _binding: first_adapter).bind_existing(binding())
        resumed_adapter = EntryAdapter()
        resumed = ZskEntry(Path("C:/runtime"), io=io, adapter_factory=lambda _binding: resumed_adapter)

        prepared = resumed.prepare_active()

        self.assertEqual(prepared.status, "ready_cached")
        self.assertEqual(resumed_adapter.calls, ["resolve", "inspect", "rules"])


if __name__ == "__main__":
    unittest.main()
