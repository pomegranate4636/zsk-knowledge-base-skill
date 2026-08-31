from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.contracts import BINDING_SCHEMA, ROOT_KEYS, Binding  # noqa: E402
from shared.fake_adapter import FakeAdapter  # noqa: E402
from shared.markdown_converter import MarkdownConversion, normalize_pptx_slide_markers, remove_unpersisted_local_images  # noqa: E402
from shared.stage5_intake import IntakeRequest, Stage5Intake  # noqa: E402
from shared.stage6_knowledge import KnowledgeRequest, Stage6Knowledge  # noqa: E402
from shared.stage7_method import MethodRequest, Stage7Method  # noqa: E402
from shared.templates import TEMPLATE_VERSION  # noqa: E402


TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"


def binding() -> Binding:
    return Binding(
        BINDING_SCHEMA, "CLT-1234567890ABCD", "验收客户", "验收知识库", "company", "obsidian",
        "/private/tmp/zsk-markitdown-test", {key: f"root:{key}" for key in ROOT_KEYS}, TEMPLATE_VERSION,
    )


class MarkdownIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = binding()
        self.adapter = FakeAdapter()
        self.adapter.resolve_binding(self.binding)
        self.adapter.create_skeleton(self.binding)
        self.intake = Stage5Intake(self.adapter)

    @mock.patch("shared.stage5_intake.convert_to_markdown")
    def test_docx_uses_markitdown_and_records_converter(self, convert) -> None:
        convert.return_value = MarkdownConversion("# 客户资料\n", "markitdown", "markitdown 0.1.6")
        response = self.intake.execute(IntakeRequest(TASK_ID, self.binding, "客户资料.docx", b"docx", "客户资料", "business_knowledge"))
        self.assertEqual((response.status, response.code), ("registered", None))
        self.assertEqual(response.evidence["conversion"], {"engine": "markitdown", "version": "markitdown 0.1.6"})
        readable = self.adapter._objects[f"source:{response.source_id}:readable"]["payload"].decode("utf-8")
        self.assertIn('conversion_engine: "markitdown"', readable)
        convert.assert_called_once_with(b"docx", ".docx")

    @mock.patch("shared.stage5_intake.convert_to_markdown")
    def test_converter_failure_only_enters_02(self, convert) -> None:
        from shared.markdown_converter import ConversionFailed
        convert.side_effect = ConversionFailed("bad output")
        response = self.intake.execute(IntakeRequest(TASK_ID, self.binding, "失败.pdf", b"pdf", "失败资料"))
        self.assertEqual((response.status, response.code), ("exception", "conversion_failed"))
        self.assertNotIn(f"source:{response.source_id}:original", self.adapter._objects)
        exceptions = [value for value in self.adapter._objects.values() if value["object_kind"] == "exception"]
        self.assertEqual(len(exceptions), 1)
        self.assertEqual(exceptions[0]["exception_data"]["reason_code"], "conversion_failed")

    def test_markdown_and_csv_keep_lightweight_local_path(self) -> None:
        response = self.intake.execute(IntakeRequest(TASK_ID, self.binding, "说明.md", "# 标题\n".encode("utf-8"), "说明"))
        self.assertEqual(response.evidence["conversion"], {"engine": "zsk-text", "version": "v1"})

    def test_pptx_slide_comments_become_visible_page_headings(self) -> None:
        text = "<!-- Slide number: 1 -->\n\n项目定位\n\n<!-- Slide number: 2 -->"
        self.assertEqual(normalize_pptx_slide_markers(text), "## 第 1 页\n\n项目定位\n\n## 第 2 页")

    def test_missing_local_image_placeholder_becomes_honest_note(self) -> None:
        converted = remove_unpersisted_local_images("正文\n\n![](图片1.jpg)\n")
        self.assertNotIn("图片1.jpg", converted)
        self.assertIn("原文图片未在轻量文字模式中保存", converted)

    def test_one_neutral_source_can_feed_03_and_04_without_folder_question(self) -> None:
        response = self.intake.execute(
            IntakeRequest(TASK_ID, self.binding, "方案.md", "# 客户服务\n\n先说明边界，再给步骤。\n".encode(), "客户服务方案")
        )
        self.assertEqual(response.record.source_role, "unknown")
        knowledge = Stage6Knowledge(self.adapter).execute(
            KnowledgeRequest(TASK_ID, self.binding, response.record, "服务边界", "客户服务", "服务前先确认适用范围。")
        )
        method = Stage7Method(self.adapter).execute(
            MethodRequest(
                TASK_ID, self.binding, response.record, "步骤式表达", "内容表达",
                "先说结论", "再拆步骤", "每段只讲一个动作", "最后给下一步", "结论后接可执行步骤",
            )
        )
        self.assertEqual((knowledge.status, knowledge.asset.source_role), ("registered", "business_knowledge"))
        self.assertEqual((method.status, method.asset.source_role), ("registered", "reference_method"))


if __name__ == "__main__":
    unittest.main()
