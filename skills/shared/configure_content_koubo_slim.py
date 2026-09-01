#!/usr/bin/env python3
"""Preview, confirm, or reuse one Content 口播 Slim handoff from ZSK."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SHARED_ROOT = Path(__file__).resolve().parent
SKILLS_ROOT = SHARED_ROOT.parent
if str(SKILLS_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILLS_ROOT))

from shared.content_koubo_slim_handoff import (  # noqa: E402
    ContentKouboSlimHandoffError,
    configure_content_koubo_slim_handoff,
)
from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, Binding  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把一个已确认的 Obsidian 知识库安全连接给 Content 口播 Slim"
    )
    parser.add_argument("--vault-root", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--registry")
    parser.add_argument("--runs-root")
    parser.add_argument(
        "--speaker-mode",
        choices=("neutral", "company_brand", "personal_ip"),
        default="neutral",
    )
    parser.add_argument("--confirmation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    vault = Path(args.vault_root).expanduser()
    subject_type = "person" if args.speaker_mode == "personal_ip" else "company"
    try:
        binding = Binding(
            BINDING_SCHEMA,
            args.client_id,
            vault.name,
            vault.name,
            subject_type,
            "obsidian",
            str(vault),
            {key: f"root:{key}" for key in ROOT_KEYS},
            TEMPLATE_VERSION,
        )
        response = configure_content_koubo_slim_handoff(
            binding=binding,
            registry_path=args.registry,
            runs_root=args.runs_root,
            speaker_mode=args.speaker_mode,
            confirmation=args.confirmation,
        )
        exit_code = 0
    except (ContentKouboSlimHandoffError, ValueError) as exc:
        response = {
            "status": "blocked",
            "status_label": "内容资料库连接未完成",
            "message": getattr(exc, "message", str(exc)),
            "next_action": "按提示修正后重新做零写入预检。",
        }
        exit_code = 2
    print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
