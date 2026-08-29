from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.feishu_auth import complete_user_authorization, start_user_authorization  # noqa: E402
from shared.feishu_cli import CliResponse  # noqa: E402


class AuthRunner:
    def __init__(self, runtime: Path) -> None:
        self.runtime = runtime
        self.calls = []

    def run(self, argv, *, stdin=None):
        actual = tuple(argv)
        self.calls.append(actual)
        if actual[1:3] == ("auth", "login") and "--no-wait" in actual:
            return CliResponse(0, json.dumps({"verification_url": "https://open.feishu.cn/device/authorize", "device_code": "challenge"}))
        if actual[1:3] == ("auth", "qrcode"):
            output = Path(actual[actual.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"png")
            return CliResponse(0, '{"ok":true}')
        if actual[1:3] == ("auth", "login") and "--device-code" in actual:
            return CliResponse(0, '{"ok":true,"data":{"status":"ready"}}')
        return CliResponse(2, "", "unexpected")

    def upload(self, argv, *, payload, name):
        return CliResponse(2, "", "unexpected")


class FeishuAuthorizationTests(unittest.TestCase):
    def test_start_opens_original_url_and_creates_qr_inside_runtime(self) -> None:
        runtime = ROOT / ".auth-test"
        opened = []
        runner = AuthRunner(runtime)
        try:
            result = start_user_authorization(runner, runtime, browser_opener=lambda url: opened.append(url) or True)
            self.assertEqual(result.verification_url, "https://open.feishu.cn/device/authorize")
            self.assertEqual(result.device_code, "challenge")
            self.assertEqual(opened, [result.verification_url])
            self.assertTrue(result.qr_path.is_file())
            self.assertEqual(result.qr_path.parent, runtime.resolve())
        finally:
            qr = runtime / "feishu-authorization.png"
            if qr.exists():
                qr.unlink()
            if runtime.exists():
                runtime.rmdir()

    def test_complete_uses_current_challenge_without_persisting_it(self) -> None:
        runtime = ROOT / ".auth-test"
        runner = AuthRunner(runtime)
        result = complete_user_authorization(runner, "challenge")
        self.assertEqual(result["status"], "ready")
        self.assertFalse(runtime.exists())


if __name__ == "__main__":
    unittest.main()
