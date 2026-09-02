from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, Binding  # noqa: E402
from shared.obsidian_adapter import ObsidianAdapter  # noqa: E402
from shared.stage5_intake import IntakeRequest, Stage5Intake  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


TASK_ID = "01a06162-5c1a-7071-9e0c-bc2c112bcec1"


def binding(locator: str) -> Binding:
    return Binding(
        BINDING_SCHEMA,
        "CLT-OBSIDIAN-CONTROL",
        "验收客户",
        "验收知识库",
        "company",
        "obsidian",
        locator,
        {key: f"root:{key}" for key in ROOT_KEYS},
        TEMPLATE_VERSION,
    )


class ObsidianControlDirectoryTests(unittest.TestCase):
    def test_standard_obsidian_directory_allows_intake(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(folder)
            adapter = ObsidianAdapter()
            self.assertEqual(adapter.resolve_binding(active_binding).status, "ok")
            self.assertEqual(adapter.create_skeleton(active_binding).status, "ok")
            (Path(folder) / ".obsidian").mkdir()

            inspected = adapter.inspect_structure(active_binding)
            self.assertEqual((inspected.status, inspected.code), ("reused", None))
            response = Stage5Intake(adapter).execute(
                IntakeRequest(
                    TASK_ID,
                    active_binding,
                    "客户资料.md",
                    b"# Customer material\n",
                    "客户资料",
                )
            )
            self.assertEqual((response.status, response.code), ("registered", None))

    def test_non_directory_obsidian_control_object_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(folder)
            adapter = ObsidianAdapter()
            self.assertEqual(adapter.resolve_binding(active_binding).status, "ok")
            self.assertEqual(adapter.create_skeleton(active_binding).status, "ok")
            (Path(folder) / ".obsidian").write_text("not a directory", encoding="utf-8")

            inspected = adapter.inspect_structure(active_binding)
            self.assertEqual((inspected.status, inspected.code), ("blocked", "structure_conflict"))

    def test_other_unknown_root_object_remains_blocked(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as folder:
            active_binding = binding(folder)
            adapter = ObsidianAdapter()
            self.assertEqual(adapter.resolve_binding(active_binding).status, "ok")
            self.assertEqual(adapter.create_skeleton(active_binding).status, "ok")
            (Path(folder) / "未知目录").mkdir()

            inspected = adapter.inspect_structure(active_binding)
            self.assertEqual((inspected.status, inspected.code), ("blocked", "structure_conflict"))


if __name__ == "__main__":
    unittest.main()
