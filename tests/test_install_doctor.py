from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

import install  # noqa: E402


class InstallDoctorTests(unittest.TestCase):
    def test_shared_requires_the_markdown_converter(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            for name in install.COMPONENTS:
                component = destination / name
                component.mkdir()
                if name != "shared":
                    (component / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
            present, missing = install.installed_state(destination)
            self.assertNotIn("shared", present)
            self.assertIn("shared", missing)

    def test_source_requires_the_markdown_converter(self) -> None:
        self.assertEqual(install.validate_source(ROOT / "skills"), [])

    def test_shared_requires_the_page_renderer_module(self) -> None:
        self.assertTrue((ROOT / "skills" / "shared" / "page_renderer.py").is_file())

    def test_markitdown_skill_is_a_required_component(self) -> None:
        self.assertIn("markitdown-skill", install.COMPONENTS)
        self.assertTrue((ROOT / "skills" / "markitdown-skill" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
