from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.content_koubo_slim_handoff import (  # noqa: E402
    ContentKouboSlimHandoffError,
    configure_content_koubo_slim_handoff,
)
import shared.content_koubo_slim_handoff as handoff  # noqa: E402
from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, Binding  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


VALID_METHOD = """---
asset_id: AST-METHOD001
type: oral_method_asset
status: active
audience_scope: both
keywords:
  - "需求确认"
use_when:
  - "需要先确认需求再给方案"
source_id: "SRC-1234567890abcdef12345678"
---

# 需求确认方法
"""

VALID_PROFILE = """---
status: active
is_primary: true
profile_id: PRF-PRIMARY001
profile_schema: zsk-profile-primary-v1
source_id: "SRC-1234567890abcdef12345678"
---

# 主 Profile
"""


class ContentKouboSlimHandoffTests(unittest.TestCase):
    def temporary_root(self):
        parent = Path("/private/tmp") if Path("/private/tmp").is_dir() else None
        return tempfile.TemporaryDirectory(prefix="zsk-content-handoff-", dir=parent)

    def make_vault(self, root: Path, *, with_profile: bool = True) -> Path:
        vault = root / "客户知识库"
        vault.mkdir()
        for name in (
            "01-来源索引",
            "02-待审核",
            "03-业务知识库",
            "04-内容方法库",
            "05-IP-Profile",
            "06-Agent与Workflow",
            "07-生产与反馈",
        ):
            (vault / name).mkdir()
        (vault / "04-内容方法库" / "方法.md").write_text(
            VALID_METHOD, encoding="utf-8"
        )
        if with_profile:
            (vault / "05-IP-Profile" / "主体.md").write_text(
                VALID_PROFILE, encoding="utf-8"
            )
        return vault

    def binding(self, vault: Path, *, backend: str = "obsidian") -> Binding:
        locator = str(vault) if backend == "obsidian" else "https://feishu.cn/wiki/space/test"
        return Binding(
            BINDING_SCHEMA,
            "CLT-1234567890AB",
            "验收主体",
            "验收知识库",
            "person",
            backend,
            locator,
            {key: f"root:{key}" for key in ROOT_KEYS},
            TEMPLATE_VERSION,
        )

    @staticmethod
    def targets(root: Path) -> tuple[Path, Path]:
        config = root / "host" / ".content-koubo-slim"
        return config / "client-registry.json", config / "runs"

    def test_preview_is_zero_write_then_confirmation_creates_and_reuses(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            registry, runs = self.targets(root)
            binding = self.binding(vault)

            preview = configure_content_koubo_slim_handoff(
                binding=binding,
                registry_path=registry,
                runs_root=runs,
                speaker_mode="personal_ip",
            )
            self.assertEqual(preview["status"], "waiting")
            self.assertIsInstance(preview["confirmation"], str)
            self.assertFalse(registry.exists())
            self.assertFalse(runs.exists())
            manifest = vault / "06-Agent与Workflow" / "content-koubo-client-manifest.json"
            self.assertFalse(manifest.exists())

            created = configure_content_koubo_slim_handoff(
                binding=binding,
                registry_path=registry,
                runs_root=runs,
                speaker_mode="personal_ip",
                confirmation=preview["confirmation"],
            )
            self.assertEqual(created["status"], "completed")
            self.assertTrue(registry.is_file())
            self.assertTrue(runs.is_dir())
            self.assertTrue(manifest.is_file())
            self.assertEqual(
                json.loads(manifest.read_text(encoding="utf-8"))["client_id"],
                binding.client_id,
            )

            reused = configure_content_koubo_slim_handoff(
                binding=binding,
                registry_path=registry,
                runs_root=runs,
                speaker_mode="personal_ip",
            )
            self.assertEqual(reused["status"], "completed")
            self.assertIsNone(reused["confirmation"])
            self.assertEqual(reused["binding"]["registry_action"], "reuse")

    def test_default_new_binding_preserves_legacy_config_and_runs(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            host = root / "host"
            legacy = host / ".content-v2-slim"
            legacy_runs = legacy / "runs"
            legacy_runs.mkdir(parents=True)
            legacy_registry = legacy / "client-registry.json"
            legacy_registry.write_text('{"legacy": true}\n', encoding="utf-8")
            legacy_run = legacy_runs / "run.json"
            legacy_run.write_text('{"state": "saved"}\n', encoding="utf-8")
            before = (legacy_registry.read_bytes(), legacy_run.read_bytes())
            vault = self.make_vault(root)

            with mock.patch.dict(os.environ, {"CODEX_HOME": str(host)}):
                preview = configure_content_koubo_slim_handoff(
                    binding=self.binding(vault),
                    speaker_mode="personal_ip",
                )
                self.assertEqual(preview["status"], "waiting")
                configure_content_koubo_slim_handoff(
                    binding=self.binding(vault),
                    speaker_mode="personal_ip",
                    confirmation=preview["confirmation"],
                )

            current = (legacy_registry.read_bytes(), legacy_run.read_bytes())
            self.assertEqual(current, before)
            self.assertTrue(
                (host / ".content-koubo-slim" / "client-registry.json").is_file()
            )
            self.assertTrue((host / ".content-koubo-slim" / "runs").is_dir())

    def test_existing_registry_is_merged_without_losing_other_client(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            registry, runs = self.targets(root)
            registry.parent.mkdir(parents=True)
            original = {
                "registry_version": "2.0",
                "clients": {
                    "other-client": {
                        "vault_root": str(root / "其他知识库"),
                        "manifest_relative_path": "config/manifest.json",
                    }
                },
            }
            registry.write_text(json.dumps(original), encoding="utf-8")
            preview = configure_content_koubo_slim_handoff(
                binding=self.binding(vault),
                registry_path=registry,
                runs_root=runs,
            )
            self.assertEqual(preview["binding"]["registry_action"], "merge")
            self.assertEqual(json.loads(registry.read_text(encoding="utf-8")), original)

            configure_content_koubo_slim_handoff(
                binding=self.binding(vault),
                registry_path=registry,
                runs_root=runs,
                confirmation=preview["confirmation"],
            )
            merged = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(merged["clients"]["other-client"], original["clients"]["other-client"])
            self.assertIn("CLT-1234567890AB", merged["clients"])

    def test_same_client_conflict_is_not_overwritten(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            registry, runs = self.targets(root)
            registry.parent.mkdir(parents=True)
            original = {
                "registry_version": "2.0",
                "clients": {
                    "CLT-1234567890AB": {
                        "vault_root": str(root / "另一个知识库"),
                        "manifest_relative_path": "config/manifest.json",
                    }
                },
            }
            registry.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaisesRegex(ContentKouboSlimHandoffError, "另一知识库"):
                configure_content_koubo_slim_handoff(
                    binding=self.binding(vault),
                    registry_path=registry,
                    runs_root=runs,
                )
            self.assertEqual(json.loads(registry.read_text(encoding="utf-8")), original)

    def test_malformed_existing_registry_stops_cleanly(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            registry, runs = self.targets(root)
            registry.parent.mkdir(parents=True)
            registry.write_text(
                json.dumps(
                    {
                        "registry_version": "2.0",
                        "clients": {
                            "other-client": {
                                "vault_root": 123,
                                "manifest_relative_path": "config/manifest.json",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ContentKouboSlimHandoffError, "必须是字符串"):
                configure_content_koubo_slim_handoff(
                    binding=self.binding(vault),
                    registry_path=registry,
                    runs_root=runs,
                )

    def test_wrong_confirmation_writes_nothing(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            registry, runs = self.targets(root)
            with self.assertRaisesRegex(ContentKouboSlimHandoffError, "确认信息"):
                configure_content_koubo_slim_handoff(
                    binding=self.binding(vault),
                    registry_path=registry,
                    runs_root=runs,
                    confirmation="wrong-preview",
                )
            self.assertFalse(registry.exists())
            self.assertFalse(runs.exists())
            self.assertFalse(
                (vault / "06-Agent与Workflow" / "content-koubo-client-manifest.json").exists()
            )

    def test_registry_change_after_preview_invalidates_confirmation(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            registry, runs = self.targets(root)
            registry.parent.mkdir(parents=True)
            original = {
                "registry_version": "2.0",
                "clients": {
                    "other-client": {
                        "vault_root": str(root / "其他知识库"),
                        "manifest_relative_path": "config/manifest.json",
                    }
                },
            }
            registry.write_text(json.dumps(original), encoding="utf-8")
            preview = configure_content_koubo_slim_handoff(
                binding=self.binding(vault),
                registry_path=registry,
                runs_root=runs,
            )
            changed = json.loads(json.dumps(original))
            changed["clients"]["third-client"] = {
                "vault_root": str(root / "第三个知识库"),
                "manifest_relative_path": "config/manifest.json",
            }
            registry.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ContentKouboSlimHandoffError, "确认信息"):
                configure_content_koubo_slim_handoff(
                    binding=self.binding(vault),
                    registry_path=registry,
                    runs_root=runs,
                    confirmation=preview["confirmation"],
                )
            self.assertEqual(json.loads(registry.read_text(encoding="utf-8")), changed)

    def test_failure_after_registry_merge_restores_original_state(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            registry, runs = self.targets(root)
            registry.parent.mkdir(parents=True)
            original = {
                "registry_version": "2.0",
                "clients": {
                    "other-client": {
                        "vault_root": str(root / "其他知识库"),
                        "manifest_relative_path": "config/manifest.json",
                    }
                },
            }
            registry.write_text(json.dumps(original), encoding="utf-8")
            preview = configure_content_koubo_slim_handoff(
                binding=self.binding(vault),
                registry_path=registry,
                runs_root=runs,
            )
            real_read_json = handoff._read_json
            calls = 0

            def fail_final_registry_read(path, label):
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise ContentKouboSlimHandoffError(
                        "readback_failed", "simulated final readback failure"
                    )
                return real_read_json(path, label)

            with mock.patch.object(
                handoff, "_read_json", side_effect=fail_final_registry_read
            ):
                with self.assertRaisesRegex(ContentKouboSlimHandoffError, "simulated"):
                    configure_content_koubo_slim_handoff(
                        binding=self.binding(vault),
                        registry_path=registry,
                        runs_root=runs,
                        confirmation=preview["confirmation"],
                    )
            self.assertEqual(json.loads(registry.read_text(encoding="utf-8")), original)
            self.assertFalse(runs.exists())
            self.assertFalse(
                (vault / "06-Agent与Workflow" / "content-koubo-client-manifest.json").exists()
            )

    def test_personal_ip_requires_one_primary_profile(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root, with_profile=False)
            registry, runs = self.targets(root)
            with self.assertRaisesRegex(ContentKouboSlimHandoffError, "必须且只能"):
                configure_content_koubo_slim_handoff(
                    binding=self.binding(vault),
                    registry_path=registry,
                    runs_root=runs,
                    speaker_mode="personal_ip",
                )

    def test_feishu_binding_stops_before_local_writes(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            registry, runs = self.targets(root)
            with self.assertRaisesRegex(ContentKouboSlimHandoffError, "Obsidian"):
                configure_content_koubo_slim_handoff(
                    binding=self.binding(vault, backend="feishu"),
                    registry_path=registry,
                    runs_root=runs,
                )
            self.assertFalse(registry.exists())
            self.assertFalse(runs.exists())

    def test_symlink_vault_is_rejected(self) -> None:
        with self.temporary_root() as directory:
            root = Path(directory)
            vault = self.make_vault(root)
            linked = root / "linked-vault"
            try:
                os.symlink(vault, linked)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows account cannot create symlinks")
                raise
            registry, runs = self.targets(root)
            with self.assertRaisesRegex(ContentKouboSlimHandoffError, "软链接"):
                configure_content_koubo_slim_handoff(
                    binding=self.binding(linked),
                    registry_path=registry,
                    runs_root=runs,
                )


if __name__ == "__main__":
    unittest.main()
