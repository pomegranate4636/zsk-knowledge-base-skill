from __future__ import annotations

from pathlib import Path
from unittest import mock
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

    def test_failed_markitdown_install_rolls_back_new_components(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "skills"
            unrelated = destination / "existing-skill"
            unrelated.mkdir(parents=True)
            with mock.patch("sys.stdout") as stdout:
                with mock.patch.object(sys, "argv", ["install.py", "--dest", str(destination), "--install-markitdown"]):
                    with mock.patch.object(install, "install_converter", return_value=False):
                        self.assertEqual(install.main(), 6)
            self.assertTrue(unrelated.is_dir())
            for name in install.COMPONENTS:
                self.assertFalse((destination / name).exists())
            rendered = "".join(call.args[0] for call in stdout.write.call_args_list if call.args)
            self.assertNotIn("安装完成", rendered)

    def test_command_failure_keeps_the_last_error_lines(self) -> None:
        completed = install.subprocess.CompletedProcess(
            args=("pipx",), returncode=1, stdout="download started\n", stderr="network interrupted\n"
        )
        with mock.patch("sys.stderr") as stderr:
            install._print_command_output(completed)
        rendered = "".join(call.args[0] for call in stderr.write.call_args_list if call.args)
        self.assertIn("network interrupted", rendered)
        self.assertIn("download started", rendered)


if __name__ == "__main__":
    unittest.main()
