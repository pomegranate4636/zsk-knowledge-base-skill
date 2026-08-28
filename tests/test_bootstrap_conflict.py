from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import AdapterResult  # noqa: E402
from shared.feishu_cli import RecordedCliCall, RecordedCliRunner  # noqa: E402
from shared.stage11_bootstrap import BootstrapRequest, FirstRunBootstrap  # noqa: E402


TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"


class BootstrapConflictTests(unittest.TestCase):
    @mock.patch("shared.stage11_bootstrap.FeishuAdapter")
    def test_existing_feishu_name_stops_before_create(self, adapter_type) -> None:
        adapter_type.return_value.doctor.return_value = AdapterResult.ok()
        runner = RecordedCliRunner((
            RecordedCliCall(
                ("lark-cli", "--as", "user", "wiki", "spaces", "list", "--page-all", "--format", "json"),
                '{"data":{"items":[{"name":"已存在测试库","space_id":"123"}]}}',
            ),
        ))
        result = FirstRunBootstrap(runner=runner).execute(BootstrapRequest(TASK_ID, "新建知识库", "feishu", "已存在测试库"))
        self.assertEqual((result.status, result.code), ("needs_input", "binding_conflict"))
        self.assertTrue(runner.exhausted)


if __name__ == "__main__":
    unittest.main()
