from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.content_source_contract import (  # noqa: E402
    ContentSourceContractError,
    build_base_manifest,
    build_empty_profile_index,
    commit_registry_plan,
    plan_registry_binding,
    stable_profile_id,
    validate_profile_index,
    write_obsidian_base_contract,
)
from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, Binding  # noqa: E402
from shared.obsidian_adapter import ObsidianAdapter  # noqa: E402
from shared.stage11_bootstrap import BootstrapRequest, FirstRunBootstrap  # noqa: E402
from shared.stage5_intake import IntakeRequest, Stage5Intake  # noqa: E402
from shared.stage8_profile import ProfileLayers, ProfileRequest, Stage8Profile  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


class ContentSourceContractTests(unittest.TestCase):
    def temporary_root(self):
        parent = Path("/private/tmp") if Path("/private/tmp").is_dir() else None
        return tempfile.TemporaryDirectory(prefix="zsk-content-source-", dir=parent)

    @staticmethod
    def make_vault(root: Path) -> Path:
        vault = root / "知识库"
        vault.mkdir()
        for name in ("03-业务知识库", "04-内容方法库", "05-IP-Profile", "06-Agent与Workflow", "07-生产与反馈"):
            (vault / name).mkdir()
        return vault

    def test_base_contract_and_registry_are_create_only_and_confirmed(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            manifest = build_base_manifest(
                client_id="CLT-1234567890ABCD",
                knowledge_base_name="测试知识库",
                backend="obsidian",
                locator=str(vault.resolve()),
            )
            index = build_empty_profile_index(knowledge_base_id=manifest["knowledge_base_id"])
            manifest_path, index_path = write_obsidian_base_contract(vault, manifest, index)
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(index_path.is_file())

            registry = root / "host" / ".content-workflows" / "knowledge-base-registry.json"
            plan = plan_registry_binding(
                manifest=manifest,
                manifest_ref="06-Agent与Workflow/content-source-manifest.json",
                profile_index_ref="06-Agent与Workflow/content-profile-index.json",
                workflows=("content-koubo-slim", "content-gzh-slim"),
                registry_path=registry,
            )
            self.assertFalse(registry.exists())
            with self.assertRaises(ContentSourceContractError):
                commit_registry_plan(plan, "wrong")
            result = commit_registry_plan(plan, plan.confirmation)
            self.assertEqual(result["readback"], "verified")
            saved = json.loads(registry.read_text(encoding="utf-8"))
            self.assertIn(plan.binding_id, saved["bindings"])

    def test_profile_index_allows_multiple_active_but_only_one_primary(self) -> None:
        with self.temporary_root() as directory:
            vault = self.make_vault(Path(directory))
            manifest = build_base_manifest(
                client_id="CLT-1234567890ABCD",
                knowledge_base_name="测试知识库",
                backend="obsidian",
                locator=str(vault.resolve()),
            )
            profiles = []
            for name, primary in (("甲", True), ("乙", False)):
                profiles.append(
                    {
                        "profile_id": stable_profile_id(manifest["client_id"], name),
                        "display_name": name,
                        "aliases": [f"{name}老师"],
                        "object_ref": f"05-IP-Profile/{name}.md",
                        "status": "active",
                        "is_primary": primary,
                        "content_sha256": hashlib.sha256(name.encode()).hexdigest(),
                    }
                )
            value = {"contract_version": "content-source-v1", "knowledge_base_id": manifest["knowledge_base_id"], "profiles": profiles, "revision": 1}
            validate_profile_index(value)
            value["profiles"][1]["is_primary"] = True
            with self.assertRaisesRegex(ContentSourceContractError, "at most one"):
                validate_profile_index(value)

    def test_duplicate_active_alias_stops(self) -> None:
        with self.temporary_root() as directory:
            vault = self.make_vault(Path(directory))
            manifest = build_base_manifest(
                client_id="CLT-1234567890ABCD",
                knowledge_base_name="测试知识库",
                backend="obsidian",
                locator=str(vault.resolve()),
            )
            digest = hashlib.sha256(b"x").hexdigest()
            value = {
                "contract_version": "content-source-v1",
                "knowledge_base_id": manifest["knowledge_base_id"],
                "profiles": [
                    {"profile_id": stable_profile_id(manifest["client_id"], "甲"), "display_name": "甲", "aliases": ["老师"], "object_ref": "05-IP-Profile/a.md", "status": "active", "is_primary": True, "content_sha256": digest},
                    {"profile_id": stable_profile_id(manifest["client_id"], "乙"), "display_name": "乙", "aliases": ["老师"], "object_ref": "05-IP-Profile/b.md", "status": "active", "is_primary": False, "content_sha256": digest},
                ],
                "revision": 1,
            }
            with self.assertRaisesRegex(ContentSourceContractError, "ambiguous"):
                validate_profile_index(value)

    def test_new_obsidian_vault_profile_write_updates_index(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            bootstrap = FirstRunBootstrap(documents_parent=root)
            first = bootstrap.execute(BootstrapRequest("01a01e29-a6ba-73a2-82e6-4ad1caa0f33b", "新建知识库", "obsidian", "测试库", "客户"))
            self.assertEqual(first.status, "confirmation_required")
            created = bootstrap.execute(BootstrapRequest("01a01e29-a6ba-73a2-82e6-4ad1caa0f33b", "新建知识库", "obsidian", "测试库", "客户", confirmation=first.confirmation))
            self.assertEqual(created.status, "created")
            vault = root / "测试库"
            client_id = FirstRunBootstrap._client_id(f"obsidian:{vault}")
            binding = Binding(BINDING_SCHEMA, client_id, "客户", "测试库", "company", "obsidian", str(vault), {key: f"root:{key}" for key in ROOT_KEYS}, TEMPLATE_VERSION)
            adapter = ObsidianAdapter()
            adapter.resolve_binding(binding)
            intake = Stage5Intake(adapter).execute(
                IntakeRequest("01a01e29-a6ba-73a2-82e6-4ad1caa0f33b", binding, "人物.md", b"# Person\n\nconfirmed", "人物", source_role="profile_material")
            )
            self.assertIsNotNone(intake.record)
            response = Stage8Profile(adapter).execute(
                ProfileRequest(
                    "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b",
                    binding,
                    intake.record,
                    "人物甲",
                    ProfileLayers(("确认事实",), ("运营设定",), ("候选素材",)),
                    True,
                    ("甲老师",),
                )
            )
            self.assertEqual(response.status, "registered")
            index = json.loads((vault / "06-Agent与Workflow" / "content-profile-index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["profiles"]), 1)
            self.assertEqual(index["profiles"][0]["profile_id"], response.primary.profile_id)
            self.assertTrue(index["profiles"][0]["is_primary"])


if __name__ == "__main__":
    unittest.main()
