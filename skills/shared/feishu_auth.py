"""Split-flow Feishu user authorization for Codex and WorkBuddy hosts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
import webbrowser

from .feishu_cli import CliRunner


@dataclass(frozen=True)
class AuthorizationChallenge:
    verification_url: str
    device_code: str
    qr_path: Path


def _json(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("feishu_authorization_response_invalid")
    try:
        value = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("feishu_authorization_response_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("feishu_authorization_response_invalid")
    return value.get("data", value) if isinstance(value.get("data", value), dict) else value


def _valid_authorization_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in {"open.feishu.cn", "open.larksuite.com"}


def start_user_authorization(
    runner: CliRunner,
    runtime_root: Path,
    *,
    browser_opener: Callable[[str], bool] = webbrowser.open,
) -> AuthorizationChallenge:
    """Start login, generate a local QR, and open the exact Feishu URL.

    The device code is returned to the current conversation only. It is never
    written to the QR directory or a durable receipt.
    """
    root = runtime_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    response = runner.run((
        "lark-cli", "auth", "login",
        "--domain", "docs", "--domain", "drive", "--domain", "wiki",
        "--no-wait", "--json",
    ))
    if response.returncode != 0:
        raise RuntimeError("feishu_authorization_start_failed")
    payload = _json(response.stdout)
    url = payload.get("verification_url") or payload.get("verification_uri_complete")
    device_code = payload.get("device_code")
    if not isinstance(url, str) or not _valid_authorization_url(url) or not isinstance(device_code, str) or not device_code or any(char.isspace() for char in device_code):
        raise RuntimeError("feishu_authorization_start_response_invalid")
    qr_path = root / "feishu-authorization.png"
    qr = runner.run(("lark-cli", "auth", "qrcode", url, "--output", str(qr_path)))
    if qr.returncode != 0 or not qr_path.is_file():
        raise RuntimeError("feishu_authorization_qrcode_failed")
    browser_opener(url)
    return AuthorizationChallenge(url, device_code, qr_path)


def complete_user_authorization(runner: CliRunner, device_code: str) -> dict:
    if not isinstance(device_code, str) or not device_code or any(char.isspace() for char in device_code):
        raise ValueError("feishu_device_code_invalid")
    response = runner.run(("lark-cli", "auth", "login", "--device-code", device_code, "--json"))
    if response.returncode != 0:
        raise RuntimeError("feishu_authorization_complete_failed")
    return _json(response.stdout)
