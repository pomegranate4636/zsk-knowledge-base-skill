from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.stage11_bootstrap import BootstrapRequest, FirstRunBootstrap

TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"


class NewVaultPresetTests(unittest.TestCase):
    def test_missing_package_blocks_before_creating_vault(self):
        from shared.oral_structure_preset import PresetError
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = FirstRunBootstrap(documents_parent=Path(directory))
            with mock.patch("shared.stage11_bootstrap.load_preset", side_effect=PresetError("预置包缺失")):
                result = bootstrap.execute(BootstrapRequest(TASK_ID, "创建知识库", "obsidian", "测试库"))
            self.assertEqual((result.status, result.code), ("blocked", "preset_invalid"))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_old_vault_status_and_skeleton_reuse_do_not_install_preset(self):
        from shared.obsidian_adapter import ObsidianAdapter
        from shared.stage2_router import Stage2Router, RouterRequest
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            router = Stage2Router(ObsidianAdapter())
            fields = dict(task_id=TASK_ID, user_input="创建知识库", backend_type="obsidian", backend_locator=str(vault),
                          client_name="客户", knowledge_base_name="旧库", subject_type="company")
            preview = router.execute(RouterRequest(**fields))
            result = router.execute(RouterRequest(**fields, confirmation=preview.confirmation))
            self.assertEqual(result.status, "created")
            for intent in ("检查状态", "创建知识库"):
                existing = Stage2Router(ObsidianAdapter()).execute(RouterRequest(**{**fields, "user_input": intent}))
                self.assertEqual(existing.status, "status" if intent == "检查状态" else "reused")
            self.assertEqual(list((vault / "04-内容方法库").iterdir()), [])

    def test_preset_failure_never_reports_complete(self):
        from shared.oral_structure_preset import PresetError
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = FirstRunBootstrap(documents_parent=Path(directory))
            preview = bootstrap.execute(BootstrapRequest(TASK_ID, "创建知识库", "obsidian", "测试库"))
            with mock.patch("shared.stage11_bootstrap.install_obsidian_preset", side_effect=PresetError("回读失败", "readback_failed")):
                result = bootstrap.execute(BootstrapRequest(TASK_ID, "创建知识库", "obsidian", "测试库", confirmation=preview.confirmation))
            self.assertEqual((result.status, result.code), ("blocked", "readback_failed"))
            self.assertIsNotNone(result.locator)
            self.assertFalse((Path(directory) / "测试库" / "06-Agent与Workflow" / "oral-structure-installation.json").exists())

    def test_package_changes_invalidate_confirmation_before_writes(self):
        from dataclasses import replace
        from shared.oral_structure_preset import load_preset
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = FirstRunBootstrap(documents_parent=Path(directory))
            preview = bootstrap.execute(BootstrapRequest(TASK_ID, "创建知识库", "obsidian", "测试库"))
            changed = replace(load_preset(), version="1.0.1", sha256="a" * 64)
            with mock.patch("shared.stage11_bootstrap.load_preset", return_value=changed):
                result = bootstrap.execute(BootstrapRequest(TASK_ID, "创建知识库", "obsidian", "测试库", confirmation=preview.confirmation))
            self.assertEqual((result.status, result.code), ("blocked", "confirmation_mismatch"))
            self.assertIsNone(result.confirmation)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_preview_and_new_vault_have_complete_independent_preset(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            bootstrap = FirstRunBootstrap(documents_parent=parent)
            request = BootstrapRequest(TASK_ID, "创建知识库", "obsidian", "测试库")
            preview = bootstrap.execute(request)
            self.assertEqual(preview.status, "confirmation_required")
            self.assertIn("口播结构", preview.preview.get("included_content", ""))
            self.assertEqual(list(parent.iterdir()), [])
            created = bootstrap.execute(BootstrapRequest(
                TASK_ID, "创建知识库", "obsidian", "测试库", confirmation=preview.confirmation,
            ))
            self.assertEqual(created.status, "created", created.message)
            vault = parent / "测试库"
            files = sorted((vault / "04-内容方法库" / "口播结构").rglob("*.md"))
            self.assertEqual(len(files), 22)
            from shared.oral_structure_preset import load_preset
            for document in load_preset().documents:
                self.assertEqual((vault / "04-内容方法库" / document.path).read_bytes(), document.content.encode("utf-8"))
            receipt = json.loads((vault / "06-Agent与Workflow" / "oral-structure-installation.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["package_version"], preview.preview["preset_version"])
            self.assertEqual(len(receipt["files"]), 22)
            for item in receipt["files"]:
                payload = (vault / item["object_ref"]).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), item["installed_sha256"])
            changed = files[0]
            changed.write_text("客户自己的修改", encoding="utf-8")
            before = {p.relative_to(vault).as_posix(): p.read_bytes() for p in vault.rglob("*") if p.is_file()}
            retry = FirstRunBootstrap(documents_parent=parent).execute(request)
            self.assertEqual(retry.code, "binding_conflict")
            self.assertEqual(before, {p.relative_to(vault).as_posix(): p.read_bytes() for p in vault.rglob("*") if p.is_file()})


class PresetPackageTests(unittest.TestCase):
    def test_full_copy_readback_detects_changed_earlier_document(self):
        from shared.oral_structure_preset import load_preset, PresetError
        from shared.oral_structure_install import install_obsidian_preset, _write_new
        preset = load_preset()
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            (vault / "04-内容方法库").mkdir()
            (vault / "06-Agent与Workflow").mkdir()
            def changed_after_write(path, payload):
                _write_new(path, payload)
                if path.name == Path(preset.documents[-1].path).name:
                    (vault / "04-内容方法库" / preset.documents[0].path).write_text("外部修改", encoding="utf-8")
            with mock.patch("shared.oral_structure_install._write_new", side_effect=changed_after_write):
                with self.assertRaises(PresetError) as raised:
                    install_obsidian_preset(vault, preset)
            self.assertEqual(raised.exception.code, "readback_failed")
            self.assertFalse((vault / "06-Agent与Workflow" / "oral-structure-installation.json").exists())

    def test_receipt_retains_original_metadata_for_independent_feishu_copy(self):
        from shared.oral_structure_preset import load_preset
        preset = load_preset()
        objects = {doc.path: ("https://feishu.cn/wiki/test", doc.sha256) for doc in preset.documents}
        receipt = preset.receipt("feishu", objects)
        for document, item in zip(preset.documents, receipt["files"]):
            self.assertIn("asset_id:", item["source_frontmatter"])
            self.assertIn("legacy_mapping_only", item["source_frontmatter"])
            self.assertIn(item["source_frontmatter"], document.content)

    def test_crlf_checkout_has_same_package_digest(self):
        import shutil
        from shared.oral_structure_preset import PACKAGE_ROOT, load_preset
        original = load_preset()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "package"
            shutil.copytree(PACKAGE_ROOT, target)
            for document in original.documents:
                (target / document.path).write_bytes(document.content.replace("\n", "\r\n").encode("utf-8"))
            self.assertEqual(load_preset(target).sha256, original.sha256)

    def test_installed_bundle_builds_without_repository_imports(self):
        import subprocess
        sys.path.insert(0, str(ROOT))
        import install
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            destination = temporary / "installed-skills"
            self.assertEqual(install.install(ROOT / "skills", destination), 0)
            script = '''
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from shared.stage11_bootstrap import BootstrapRequest, FirstRunBootstrap
parent = Path(sys.argv[2])
bootstrap = FirstRunBootstrap(documents_parent=parent)
fields = dict(task_id="01a01e29-a6ba-73a2-82e6-4ad1caa0f33b", user_input="创建知识库", backend_type="obsidian", knowledge_base_name="独立安装验证")
preview = bootstrap.execute(BootstrapRequest(**fields))
result = bootstrap.execute(BootstrapRequest(**fields, confirmation=preview.confirmation))
assert result.status == "created", result.message
assert len(list((Path(result.locator) / "04-内容方法库" / "口播结构").rglob("*.md"))) == 22
assert Path(sys.modules["shared.stage11_bootstrap"].__file__).is_relative_to(Path(sys.argv[1]))
print("installed runtime: 22 documents verified")
'''
            completed = subprocess.run(
                (sys.executable, "-X", "utf8", "-I", "-c", script, str(destination), str(temporary)),
                cwd=temporary, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("22 documents verified", completed.stdout)

    def test_symlinked_target_is_rejected(self):
        from shared.oral_structure_preset import load_preset, PresetError
        from shared.oral_structure_install import install_obsidian_preset
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault, outside = root / "vault", root / "outside"
            vault.mkdir()
            outside.mkdir()
            (vault / "06-Agent与Workflow").mkdir()
            try:
                os.symlink(outside, vault / "04-内容方法库", target_is_directory=True)
            except OSError:
                self.skipTest("Host does not allow creation of directory symlinks")
            with self.assertRaises(PresetError):
                install_obsidian_preset(vault, load_preset())
            self.assertEqual(list(outside.iterdir()), [])

    def test_package_contains_resolvable_cards_and_preserves_source_status(self):
        from shared.oral_structure_preset import load_preset, feishu_markdown
        preset = load_preset()
        self.assertEqual(len(preset.documents), 22)
        self.assertEqual(len(preset.directories), 7)
        urls = {Path(doc.path).stem: f"https://feishu.cn/wiki/n{index}" for index, doc in enumerate(preset.documents)}
        for doc in preset.documents:
            self.assertIn('source_verification: "legacy_mapping_only"', doc.content)
            rendered = feishu_markdown(doc, urls)
            self.assertNotIn("[[", rendered)
            self.assertFalse(rendered.startswith("---"))

    def test_missing_tampered_and_unsafe_members_rejected(self):
        import shutil
        from shared.oral_structure_preset import PACKAGE_ROOT, PresetError, load_preset
        for kind in ("missing", "tampered", "escape", "extra", "bad_link", "empty_path", "dot_path"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "package"
                shutil.copytree(PACKAGE_ROOT, target)
                manifest_path = target / "package.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                item = manifest["files"][0]
                document = target / item["path"]
                if kind == "missing":
                    document.unlink()
                elif kind == "tampered":
                    document.write_text("changed", encoding="utf-8")
                elif kind == "escape":
                    item["path"] = "../outside.md"
                elif kind == "empty_path":
                    item["path"] = ""
                elif kind == "dot_path":
                    item["path"] = "."
                elif kind == "extra":
                    (target / "extra.md").write_text("extra", encoding="utf-8")
                else:
                    document.write_text("[[不存在]]", encoding="utf-8")
                    item["sha256"] = hashlib.sha256(document.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(PresetError):
                    load_preset(target)

    def test_existing_seed_is_never_overwritten_or_filled(self):
        from shared.oral_structure_preset import load_preset, PresetError
        from shared.oral_structure_install import install_obsidian_preset
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            seed = vault / "04-内容方法库" / "口播结构"
            seed.mkdir(parents=True)
            (vault / "06-Agent与Workflow").mkdir()
            custom = seed / "客户结构.md"
            custom.write_text("客户修改", encoding="utf-8")
            with self.assertRaises(PresetError):
                install_obsidian_preset(vault, load_preset())
            self.assertEqual(list(seed.iterdir()), [custom])
            self.assertEqual(custom.read_text(encoding="utf-8"), "客户修改")


if __name__ == "__main__":
    unittest.main()
