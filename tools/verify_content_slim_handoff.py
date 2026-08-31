#!/usr/bin/env python3
"""Verify a fresh installed ZSK-to-Content-Slim Obsidian handoff."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"


def _install_content_copy(content_root: Path, destination: Path) -> None:
    for name in (
        "content-slim",
        "content-analyzer",
        "content-context-retriever",
        "content-writer",
        "content-publish-pack",
    ):
        source = content_root / "Skills" / name
        target = destination / name
        if not (source / "SKILL.md").is_file() or target.exists():
            raise RuntimeError(f"Content install source or target is invalid: {name}")
        shutil.copytree(source, target)


def verify(content_root: Path) -> dict[str, object]:
    if not (content_root / "Skills" / "content-slim" / "SKILL.md").is_file():
        raise RuntimeError("Content Slim root is incomplete")
    content_verified = subprocess.run(
        [sys.executable, "-B", str(content_root / "tools" / "verify.py")],
        cwd=content_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    if content_verified.returncode != 0:
        raise RuntimeError(
            "Content Slim verification failed: "
            + content_verified.stdout
            + content_verified.stderr
        )
    temporary_parent = (
        Path("/private/tmp")
        if Path("/private/tmp").is_dir()
        else Path(tempfile.gettempdir()).resolve()
    )
    with tempfile.TemporaryDirectory(
        prefix="zsk-content-fresh-", dir=temporary_parent
    ) as directory:
        parent = Path(directory)
        installed_skills = parent / "installed-skills"
        installed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "install.py"),
                "--dest",
                str(installed_skills),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if installed.returncode != 0:
            raise RuntimeError(f"ZSK fresh install failed: {installed.stderr}")
        _install_content_copy(content_root, installed_skills)

        sys.path.insert(0, str(installed_skills))
        from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, Binding
        from shared.obsidian_adapter import ObsidianAdapter
        from shared.stage5_intake import IntakeRequest, Stage5Intake
        from shared.stage6_knowledge import KnowledgeRequest, Stage6Knowledge
        from shared.stage7_method import MethodRequest, Stage7Method
        from shared.stage8_profile import ProfileLayers, ProfileRequest, Stage8Profile
        from shared.templates import TEMPLATE_VERSION

        vault = parent / "vault"
        vault.mkdir(mode=0o700)
        host_root = parent / "host"
        registry = host_root / ".content-v2-slim" / "client-registry.json"
        runs = host_root / ".content-v2-slim" / "runs"
        reference = parent / "reference.md"
        reference.write_text(
            "# 参考\n\n先确认需求，再形成书面方案。\n", encoding="utf-8"
        )
        binding = Binding(
            BINDING_SCHEMA,
            "CLT-BRIDGE001",
            "桥接验收主体",
            "桥接验收知识库",
            "person",
            "obsidian",
            str(vault),
            {key: f"root:{key}" for key in ROOT_KEYS},
            TEMPLATE_VERSION,
        )
        adapter = ObsidianAdapter()
        if adapter.resolve_binding(binding).status != "ok":
            raise RuntimeError("ZSK binding resolution failed")
        if adapter.create_skeleton(binding).status != "ok":
            raise RuntimeError("ZSK skeleton creation failed")
        intake = Stage5Intake(adapter)

        def source(name: str, text: str, role: str):
            response = intake.execute(
                IntakeRequest(
                    TASK_ID,
                    binding,
                    name,
                    text.encode("utf-8"),
                    name,
                    source_role=role,
                )
            )
            if response.record is None:
                raise RuntimeError(f"ZSK intake failed for {role}")
            return response.record

        knowledge_source = source(
            "knowledge.txt",
            "确认需求后再形成书面方案。",
            "business_knowledge",
        )
        method_source = source(
            "method.txt",
            "先说顾虑，再给判断顺序。",
            "reference_method",
        )
        profile_source = source(
            "profile.txt",
            "桥接验收主体的确认资料。",
            "profile_material",
        )
        knowledge = Stage6Knowledge(adapter).execute(
            KnowledgeRequest(
                TASK_ID,
                binding,
                knowledge_source,
                "客户需求确认流程",
                "客户交付流程",
                "确认需求后，再形成书面方案。",
            )
        )
        method = Stage7Method(adapter).execute(
            MethodRequest(
                TASK_ID,
                binding,
                method_source,
                "顾虑到判断的表达结构",
                "读者决策表达",
                "从读者顾虑切入",
                "先拆顾虑再给判断顺序",
                "用对比句说具体",
                "邀请读者自查",
                "把问题判断和行动连成短链",
            )
        )
        profile = Stage8Profile(adapter).execute(
            ProfileRequest(
                TASK_ID,
                binding,
                profile_source,
                "桥接验收主体",
                ProfileLayers(
                    ("主体已确认当前业务范围。",),
                    ("当前按已确认流程运营。",),
                    ("候选素材须人工确认。",),
                ),
            )
        )
        if (knowledge.status, method.status, profile.status) != (
            "registered",
            "registered",
            "registered",
        ):
            raise RuntimeError("ZSK did not generate 03/04/05")

        handoff_env = os.environ.copy()
        handoff_env["CODEX_HOME"] = str(host_root)
        handoff_env["PYTHONDONTWRITEBYTECODE"] = "1"
        handoff_cli = installed_skills / "shared" / "configure_content_slim.py"
        handoff_command = [
            sys.executable,
            "-B",
            str(handoff_cli),
            "--vault-root",
            str(vault),
            "--client-id",
            binding.client_id,
            "--speaker-mode",
            "personal_ip",
        ]
        preview_process = subprocess.run(
            handoff_command,
            cwd=parent,
            env=handoff_env,
            capture_output=True,
            text=True,
            check=False,
        )
        preview = json.loads(preview_process.stdout)
        manifest = vault / "06-Agent与Workflow" / "content-client-manifest.json"
        if (
            preview_process.returncode != 0
            or preview.get("status") != "waiting"
            or registry.exists()
            or runs.exists()
            or manifest.exists()
        ):
            raise RuntimeError("handoff preview was not zero-write")
        completed_process = subprocess.run(
            [*handoff_command, "--confirmation", str(preview["confirmation"])],
            cwd=parent,
            env=handoff_env,
            capture_output=True,
            text=True,
            check=False,
        )
        completed = json.loads(completed_process.stdout)
        if completed_process.returncode != 0 or completed.get("status") != "completed":
            raise RuntimeError("handoff confirmation did not complete")

        content_skill = installed_skills / "content-slim"
        sys.path.insert(0, str(content_skill))
        from runtime.client_manifest import load_manifest
        from runtime.client_registry import load_registry, resolve_client, select_client_id
        from runtime.vault_reader import read_primary_profile
        from runtime.vault_search import search_knowledge_assets, search_method_assets

        loaded_registry = load_registry(registry)
        selected_client = select_client_id(loaded_registry)
        location = resolve_client(loaded_registry, selected_client)
        load_manifest(location.manifest_path, expected_client_id=selected_client)
        knowledge_assets = search_knowledge_assets(
            vault / "03-业务知识库", needs=["客户交付流程"]
        )
        method_assets = search_method_assets(
            vault / "04-内容方法库", query="读者决策表达"
        )
        read_primary_profile(
            vault / "05-IP-Profile", {"status": "active", "is_primary": True}
        )
        if len(knowledge_assets) != 1 or len(method_assets) != 1:
            raise RuntimeError("Content Slim did not read ZSK 03/04")

        env = os.environ.copy()
        env["CODEX_HOME"] = str(host_root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        started = subprocess.run(
            [
                sys.executable,
                "-B",
                str(content_skill / "scripts" / "content_slim.py"),
                "start",
                "--topic-original",
                "如何先确认需求再给方案",
                "--reference",
                str(reference),
            ],
            cwd=parent,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            started_payload = json.loads(started.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Content start returned invalid JSON: {started.stderr}") from exc
        if started.returncode != 0 or not started_payload.get("run_created_now"):
            raise RuntimeError(f"Content automatic first run failed: {started_payload}")
        if any((vault / "07-生产与反馈").rglob("*.md")):
            raise RuntimeError("verification unexpectedly saved final content")
        reused_process = subprocess.run(
            handoff_command,
            cwd=parent,
            env=handoff_env,
            capture_output=True,
            text=True,
            check=False,
        )
        reused = json.loads(reused_process.stdout)
        if (
            reused_process.returncode != 0
            or reused.get("status") != "completed"
            or reused.get("confirmation") is not None
        ):
            raise RuntimeError("confirmed handoff was not reusable")
        return {
            "status": "passed",
            "fresh_zsk_install": True,
            "fresh_content_install": True,
            "content_package_verified": True,
            "zsk_assets": {"03": 1, "04": 1, "05": 1},
            "preview_wrote": False,
            "automatic_client_selection": selected_client == binding.client_id,
            "content_run_created": True,
            "final_output_created": False,
            "binding_reused": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="验证 ZSK 到独立 Content Slim 仓库的本地交接"
    )
    parser.add_argument("--content-slim-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.content_slim_root.resolve())
    except Exception as exc:
        result = {"status": "failed", "message": str(exc)}
        exit_code = 1
    else:
        exit_code = 0
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
