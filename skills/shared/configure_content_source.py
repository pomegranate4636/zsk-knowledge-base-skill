#!/usr/bin/env python3
"""Zero-write preview and confirmed Host Registry configuration for Content workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.content_source_contract import (  # type: ignore
        ContentSourceContractError,
        commit_registry_plan,
        plan_registry_binding,
        validate_manifest,
    )
else:
    from .content_source_contract import (
        ContentSourceContractError,
        commit_registry_plan,
        plan_registry_binding,
        validate_manifest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure a content-source-v1 Host binding")
    parser.add_argument("--manifest", required=True, type=Path, help="local readable copy of content-source-manifest.json")
    parser.add_argument("--manifest-ref", required=True, help="knowledge-base-local path or stable Feishu object ref")
    parser.add_argument("--profile-index-ref", required=True, help="knowledge-base-local path or stable Feishu object ref")
    parser.add_argument("--workflow", action="append", required=True, choices=("content-koubo-slim", "content-gzh-slim"))
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--confirmation")
    args = parser.parse_args(argv)
    try:
        manifest = validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
        plan = plan_registry_binding(
            manifest=manifest,
            manifest_ref=args.manifest_ref,
            profile_index_ref=args.profile_index_ref,
            workflows=tuple(dict.fromkeys(args.workflow)),
            registry_path=args.registry,
        )
        if args.confirmation:
            response = commit_registry_plan(plan, args.confirmation)
        else:
            response = {
                "status": "confirmation_required",
                "message": "零写入预览已生成；确认后才登记宿主 Registry。",
                "preview": {
                    "registry_path": str(plan.registry_path),
                    "binding_id": plan.binding_id,
                    "registry_action": plan.action,
                    "workflows": list(dict.fromkeys(args.workflow)),
                    "wrote": False,
                },
                "confirmation": plan.confirmation,
            }
        print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ContentSourceContractError) as exc:
        print(json.dumps({"status": "blocked", "message": str(exc), "wrote": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
