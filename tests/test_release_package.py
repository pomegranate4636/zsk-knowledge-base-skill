from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_release_package", ROOT / "tools" / "build_release_package.py")
assert SPEC is not None and SPEC.loader is not None
build_release_package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_release_package)


class ReleasePackageTests(unittest.TestCase):
    def test_selects_only_installation_files_and_excludes_bytecode(self) -> None:
        tracked = (
            "README.md",
            "install.py",
            "requirements-markitdown.txt",
            "skills/zsk-router/SKILL.md",
            "skills/shared/contracts.py",
            "skills/shared/__pycache__/contracts.cpython-312.pyc",
            "schemas/content-source-manifest.schema.json",
            "tests/test_install_doctor.py",
            "tools/verify_content_source_v1.py",
            ".github/workflows/test.yml",
        )
        self.assertEqual(
            build_release_package.select_release_members(tracked),
            (
                "README.md",
                "install.py",
                "requirements-markitdown.txt",
                "schemas/content-source-manifest.schema.json",
                "skills/shared/contracts.py",
                "skills/zsk-router/SKILL.md",
            ),
        )

    def test_archive_records_commit_and_contains_no_cache_entries(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            members = (
                "README.md",
                "install.py",
                "requirements-markitdown.txt",
                "schemas/content-source-manifest.schema.json",
                "skills/zsk-router/SKILL.md",
            )
            for relative in members:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative + "\n", encoding="utf-8")
            output = root / "dist" / "ZSK.zip"
            build_release_package.write_release_archive(root, output, members, "a" * 40)
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                manifest = json.loads(archive.read("BUILD-MANIFEST.json"))
            self.assertEqual(set(names), {*members, "BUILD-MANIFEST.json"})
            self.assertFalse(any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names))
            self.assertEqual(manifest["source_commit"], "a" * 40)
            self.assertEqual(manifest["file_count"], len(members))


if __name__ == "__main__":
    unittest.main()
