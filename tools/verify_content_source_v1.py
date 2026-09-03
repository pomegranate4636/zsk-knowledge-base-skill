#!/usr/bin/env python3
"""Isolated cross-repository acceptance for ZSK, Koubo, and GZH."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.content_source_contract import (  # noqa: E402
    build_base_manifest,
    stable_binding_id,
    stable_knowledge_base_id,
    stable_profile_id,
    write_obsidian_base_contract,
)


def _run(arguments: list[str], *, cwd: Path, env: dict[str, str]) -> dict:
    completed = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"command returned invalid JSON: {completed.stderr}") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {value}")
    return value


def _profile(vault: Path, client_id: str, name: str, primary: bool) -> dict:
    profile_id = stable_profile_id(client_id, name)
    content = (
        "---\nstatus: active\n"
        f"is_primary: {'true' if primary else 'false'}\n"
        f"profile_id: {profile_id}\nprofile_schema: zsk-profile-v2\n"
        f"display_name: {json.dumps(name, ensure_ascii=False)}\n"
        f"aliases: [{json.dumps(name + '老师', ensure_ascii=False)}]\n---\n\n"
        f"# {name} Profile\n\n## 确认事实\n\n- {name}只讲可核验事实。\n- {name}面向企业负责人。\n- {name}不补造客户结果。\n"
    )
    path = vault / "05-IP-Profile" / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return {
        "profile_id": profile_id,
        "display_name": name,
        "aliases": [name + "老师"],
        "object_ref": path.relative_to(vault).as_posix(),
        "status": "active",
        "is_primary": primary,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def verify(koubo_root: Path, gzh_root: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="content-source-v1-") as directory:
        root = Path(directory)
        host = root / "host"
        registry = host / ".content-workflows" / "knowledge-base-registry.json"
        vault = root / "knowledge-base"
        vault.mkdir()
        for name in ("03-业务知识库", "04-内容方法库", "05-IP-Profile", "06-Agent与Workflow", "07-生产与反馈"):
            (vault / name).mkdir()
        client_id = "CLT-CROSSREPO001"
        profiles = [_profile(vault, client_id, "甲", True), _profile(vault, client_id, "乙", False)]
        manifest = build_base_manifest(client_id=client_id, knowledge_base_name="跨仓隔离知识库", backend="obsidian", locator=str(vault.resolve()))
        profile_index = {"contract_version": "content-source-v1", "knowledge_base_id": manifest["knowledge_base_id"], "profiles": profiles, "revision": 1}
        write_obsidian_base_contract(vault, manifest, profile_index)
        (vault / "03-业务知识库" / "事实.md").write_text("---\nasset_id: KNO-CROSS\ntype: business_knowledge_asset\nstatus: confirmed\nkeywords:\n  - 企业服务\napplicable_workflows:\n  - content-gzh-slim\n  - content-koubo-slim\n---\n\n# 企业服务事实\n\n先确认需求再给方案。\n", encoding="utf-8")
        (vault / "04-内容方法库" / "方法.md").write_text("---\nasset_id: MET-CROSS\ntype: content_method_asset\nstatus: active\naudience_scope: both\nkeywords:\n  - 企业服务\nuse_when:\n  - 解释服务流程\napplicable_workflows:\n  - content-gzh-slim\n  - content-koubo-slim\n---\n\n# 服务流程方法\n\n先判断，再行动。\n", encoding="utf-8")
        env = {**os.environ, "CODEX_HOME": str(host), "PYTHONDONTWRITEBYTECODE": "1"}
        koubo_cli = koubo_root / "Skills" / "content-koubo-slim" / "scripts" / "content_koubo_slim.py"
        preview = _run([sys.executable, "-B", str(koubo_cli), "configure", "--vault", str(vault), "--registry", str(registry), "--client-id", client_id], cwd=root, env=env)
        if preview.get("preview", {}).get("wrote") is not False or registry.exists():
            raise RuntimeError("Koubo configuration preview was not zero-write")
        _run([sys.executable, "-B", str(koubo_cli), "configure", "--vault", str(vault), "--registry", str(registry), "--client-id", client_id, "--confirmation", preview["confirmation"]], cwd=root, env=env)
        gzh_cli = gzh_root / "scripts" / "content-gzh-slim"
        gzh_preview = _run([sys.executable, "-B", str(gzh_cli), "configure", "--knowledge-base", str(vault), "--registry", str(registry)], cwd=root, env=env)
        _run([sys.executable, "-B", str(gzh_cli), "configure", "--knowledge-base", str(vault), "--registry", str(registry), "--confirmation", gzh_preview["confirmation"]], cwd=root, env=env)
        configured = json.loads(registry.read_text(encoding="utf-8"))
        binding = next(iter(configured["bindings"].values()))
        if set(binding["supported_workflows"]) != {"content-koubo-slim", "content-gzh-slim"}:
            raise RuntimeError("the two Content products did not share one binding")
        reference = root / "reference.md"
        reference.write_text("# 参考\n\n先找真正的问题，再给一个可以验证的下一步。\n", encoding="utf-8")
        koubo_records = []
        for name in ("甲", "乙"):
            started = _run([sys.executable, "-B", str(koubo_cli), "start", "--registry", str(registry), "--runs-root", str(root / "koubo-runs"), "--speaker-mode", "personal_ip", "--profile", name, "--topic-original", "企业服务怎么做", "--reference", str(reference)], cwd=root, env=env)
            if not started.get("run_created_now"):
                raise RuntimeError("Koubo did not create an isolated Profile Run")
            task_records = sorted((root / "koubo-runs" / "task-keys").glob("*.json"))
            koubo_records.append(len(task_records))
        if koubo_records != [1, 2]:
            raise RuntimeError("Koubo Profile Runs were not isolated")
        gzh_ids = []
        for name in ("甲", "乙"):
            task_path = root / f"gzh-{name}.json"
            task_path.write_text(json.dumps({"knowledge_base": manifest["knowledge_base_id"], "ip": name, "topic": "企业服务怎么做", "references": []}, ensure_ascii=False), encoding="utf-8")
            started = _run([sys.executable, "-B", str(gzh_cli), "start", "--input", str(task_path), "--registry", str(registry), "--store", str(root / "gzh-runs")], cwd=root, env=env)
            gzh_ids.append(started["run_id"])
        if len(set(gzh_ids)) != 2:
            raise RuntimeError("GZH Profile Runs were not isolated")
        feishu_kb = stable_knowledge_base_id("feishu", "https://feishu.cn/wiki/space/123")
        feishu_binding = stable_binding_id(client_id, feishu_kb)
        configured["bindings"][feishu_binding] = {
            "binding_id": feishu_binding,
            "client_id": client_id,
            "knowledge_base_id": feishu_kb,
            "backend": "feishu",
            "locator": {"knowledge_base_ref": "https://feishu.cn/wiki/space/123"},
            "manifest_ref": "manifest-token",
            "profile_index_ref": "profile-token",
            "supported_workflows": ["content-koubo-slim"],
            "workflow_defaults": {"content-koubo-slim": {"profile_id": None, "use_no_ip": False}},
            "status": "active",
        }
        configured["revision"] += 1
        registry.write_text(json.dumps(configured, ensure_ascii=False), encoding="utf-8")
        stopped = subprocess.run([sys.executable, "-B", str(koubo_cli), "start", "--registry", str(registry), "--binding-id", feishu_binding, "--speaker-mode", "neutral", "--topic-original", "测试飞书阻断", "--reference", str(reference)], cwd=root, env=env, capture_output=True, text=True)
        if stopped.returncode != 2 or "当前知识库后端不能" not in stopped.stdout:
            raise RuntimeError("Koubo did not fail closed on Feishu")
        return {
            "status": "passed",
            "shared_binding": True,
            "profile_count": 2,
            "koubo_isolated_runs": 2,
            "gzh_isolated_runs": 2,
            "koubo_feishu_fail_closed": True,
            "real_customer_data_written": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-koubo-slim-root", required=True, type=Path)
    parser.add_argument("--content-gzh-slim-root", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.content_koubo_slim_root.resolve(), args.content_gzh_slim_root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
