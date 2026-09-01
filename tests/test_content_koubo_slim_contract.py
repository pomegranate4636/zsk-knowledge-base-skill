from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, Binding  # noqa: E402
from shared.fake_adapter import FakeAdapter  # noqa: E402
from shared.feishu_adapter import _visible_asset_body  # noqa: E402
from shared.stage5_intake import IntakeRequest, Stage5Intake  # noqa: E402
from shared.stage7_method import MethodRequest, Stage7Method  # noqa: E402
from shared.stage8_profile import ProfileLayers, ProfileRequest, Stage8Profile  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"


class ContentKouboSlimContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = Binding(
            BINDING_SCHEMA,
            "CLT-1234567890ABCD",
            "验收主体",
            "验收知识库",
            "person",
            "obsidian",
            "/private/tmp/zsk-content-koubo-slim-contract",
            {key: f"root:{key}" for key in ROOT_KEYS},
            TEMPLATE_VERSION,
        )
        self.adapter = FakeAdapter()
        self.adapter.resolve_binding(self.binding)
        self.adapter.create_skeleton(self.binding)
        self.intake = Stage5Intake(self.adapter)

    def source(self, name: str, role: str):
        response = self.intake.execute(
            IntakeRequest(
                TASK_ID,
                self.binding,
                name,
                f"# {name}\n\n确认内容。\n".encode(),
                name,
                source_role=role,
            )
        )
        self.assertIsNotNone(response.record)
        return response.record

    def test_method_card_has_content_koubo_slim_selectable_frontmatter(self) -> None:
        source = self.source("表达方法.md", "reference_method")
        response = Stage7Method(self.adapter).execute(
            MethodRequest(
                TASK_ID,
                self.binding,
                source,
                "顾虑到判断",
                "读者决策表达",
                "先说顾虑",
                "再给判断顺序",
                "使用短对比句",
                "邀请读者自查",
                "把问题判断和行动连成短链",
            )
        )
        self.assertEqual(response.status, "registered")
        self.assertIsNotNone(response.asset)
        body = response.asset.body
        self.assertIn(f"asset_id: {response.asset.asset_id}\n", body)
        self.assertIn("type: oral_method_asset\n", body)
        self.assertIn("status: active\n", body)
        self.assertIn("audience_scope: both\n", body)
        self.assertIn('keywords:\n  - "读者决策表达"\n', body)
        self.assertIn(
            'use_when:\n  - "把问题判断和行动连成短链"\n',
            body,
        )

    def test_profile_has_one_active_primary_selector(self) -> None:
        source = self.source("主体资料.md", "profile_material")
        response = Stage8Profile(self.adapter).execute(
            ProfileRequest(
                TASK_ID,
                self.binding,
                source,
                "验收主体",
                ProfileLayers(
                    ("主体已确认当前业务范围。",),
                    ("当前按已确认流程运营。",),
                    ("候选素材须人工确认。",),
                ),
            )
        )
        self.assertEqual(response.status, "registered")
        self.assertIsNotNone(response.primary)
        body = response.primary.body()
        self.assertIn("status: active\n", body)
        self.assertIn("is_primary: true\n", body)
        self.assertIn(f"profile_id: {response.primary.profile_id}\n", body)
        self.assertIn("profile_schema: zsk-profile-primary-v1\n", body)

    def test_feishu_visible_body_hides_bridge_frontmatter(self) -> None:
        body = (
            "---\nstatus: active\nis_primary: true\n---\n\n"
            "# 主体 Profile\n\n客户可见正文。\n"
        )
        visible = _visible_asset_body(body).decode("utf-8")
        self.assertEqual(
            visible, "# 主体 Profile\n\n客户可见正文。\n"
        )
        self.assertNotIn("is_primary", visible)


if __name__ == "__main__":
    unittest.main()
