from __future__ import annotations

from pathlib import Path
import os
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

    def test_package_check_accepts_the_complete_repository(self) -> None:
        self.assertEqual(install.validate_package(ROOT), [])

    def test_package_check_reports_missing_contract_schema(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            package_root = Path(folder)
            (package_root / "README.md").write_text("# package\n", encoding="utf-8")
            (package_root / "install.py").write_text("# installer\n", encoding="utf-8")
            (package_root / "requirements-markitdown.txt").write_text("markitdown\n", encoding="utf-8")
            (package_root / "skills").mkdir()
            with mock.patch.object(install, "validate_source", return_value=[]):
                errors = install.validate_package(package_root)
            self.assertTrue(any("content-profile-index.schema.json" in error for error in errors))

    def test_shared_requires_the_page_renderer_module(self) -> None:
        self.assertTrue((ROOT / "skills" / "shared" / "page_renderer.py").is_file())

    def test_shared_requires_page_text_and_local_ocr_modules(self) -> None:
        self.assertTrue((ROOT / "skills" / "shared" / "page_text.py").is_file())
        self.assertTrue((ROOT / "skills" / "shared" / "ocr_provider.py").is_file())
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            shared = destination / "shared"
            shared.mkdir()
            for name in (
                "markdown_converter.py",
                "page_renderer.py",
                "content_koubo_slim_handoff.py",
                "configure_content_koubo_slim.py",
                "naming.py",
                "page_text.py",
            ):
                (shared / name).write_text("", encoding="utf-8")
            for name in install.COMPONENTS:
                if name == "shared":
                    continue
                component = destination / name
                component.mkdir()
                (component / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
            present, missing = install.installed_state(destination)
            self.assertNotIn("shared", present)
            self.assertIn("shared", missing)

    def test_shared_requires_content_source_contract_and_ocr_together(self) -> None:
        self.assertTrue(
            {
                "content_source_contract.py",
                "configure_content_source.py",
                "page_text.py",
                "ocr_provider.py",
            }.issubset(install.SHARED_REQUIRED_FILES)
        )

    @mock.patch.object(install.shutil, "which", return_value="tesseract.exe")
    @mock.patch.object(
        install.subprocess,
        "run",
        return_value=install.subprocess.CompletedProcess(
            ("tesseract.exe", "--list-langs"), 0, "List of available languages (2):\nchi_sim\neng\n", ""
        ),
    )
    def test_doctor_reports_local_ocr_languages(self, _run, _which) -> None:
        status = install.local_ocr_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["engine"], "Tesseract")
        self.assertEqual(status["languages"], ("chi_sim", "eng"))

    def test_windows_standard_tesseract_path_works_before_path_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "Tesseract-OCR" / "tesseract.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"exe")
            with mock.patch.dict(os.environ, {"ProgramFiles": folder}, clear=False):
                with mock.patch.object(install.platform, "system", return_value="Windows"):
                    with mock.patch.object(install.shutil, "which", return_value=None):
                        self.assertEqual(install.tesseract_executable(), str(executable))

    def test_doctor_uses_user_level_tessdata_directory(self) -> None:
        tessdata = Path("C:/fake/tessdata")
        completed = install.subprocess.CompletedProcess(("tesseract",), 0, "chi_sim\neng\n", "")
        with mock.patch.object(install, "tesseract_executable", return_value="tesseract.exe"):
            with mock.patch.object(install, "tessdata_directory", return_value=tessdata):
                with mock.patch.object(install.subprocess, "run", return_value=completed) as run:
                    status = install.local_ocr_status()
            self.assertTrue(status["ready"])
            self.assertEqual(run.call_args.args[0], ("tesseract.exe", "--tessdata-dir", str(tessdata), "--list-langs"))

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

    def test_doctor_prefers_native_powerpoint_on_mac(self) -> None:
        with mock.patch.object(install, "_mac_powerpoint_ready", return_value=True):
            with mock.patch.object(install, "_optional_binary_ready", return_value=True):
                status = install.page_evidence_status()
        self.assertTrue(status["pptx"])
        self.assertEqual(status["pptx_engine"], "Microsoft PowerPoint")

    def test_doctor_prefers_native_powerpoint_on_windows(self) -> None:
        with mock.patch.object(install, "_windows_powerpoint_ready", return_value=True, create=True):
            with mock.patch.object(install, "_mac_powerpoint_ready", return_value=False):
                with mock.patch.object(install, "_optional_binary_ready", return_value=True):
                    status = install.page_evidence_status()
        self.assertTrue(status["pptx"])
        self.assertEqual(status["pptx_engine"], "Microsoft PowerPoint")

    @mock.patch.object(install.platform, "system", return_value="Windows")
    @mock.patch.object(install.shutil, "which", return_value="powershell.exe")
    @mock.patch.object(
        install.subprocess,
        "run",
        return_value=install.subprocess.CompletedProcess(("powershell.exe",), 0, "", ""),
    )
    def test_windows_powerpoint_detection_uses_registered_com(self, _run, _which, _system) -> None:
        self.assertTrue(install._windows_powerpoint_ready())

    def test_windows_powerpoint_detection_fails_closed(self) -> None:
        with mock.patch.object(install.platform, "system", return_value="Linux"):
            self.assertFalse(install._windows_powerpoint_ready())
        with mock.patch.object(install.platform, "system", return_value="Windows"):
            with mock.patch.object(install.shutil, "which", return_value=None):
                self.assertFalse(install._windows_powerpoint_ready())
        with mock.patch.object(install.platform, "system", return_value="Windows"):
            with mock.patch.object(install.shutil, "which", return_value="powershell.exe"):
                with mock.patch.object(
                    install.subprocess,
                    "run",
                    side_effect=install.subprocess.TimeoutExpired(("powershell.exe",), 15),
                ):
                    self.assertFalse(install._windows_powerpoint_ready())


if __name__ == "__main__":
    unittest.main()
