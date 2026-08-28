"""阶段 2 建库骨架的唯一模板真源。"""

from __future__ import annotations

import hashlib
from typing import Mapping

from .contracts import Binding, ROOT_KEYS


TEMPLATE_VERSION = "zsk-stage2-template-v1"
ROOT_TITLES = {
    "01": "01-来源索引",
    "02": "02-待审核",
    "03": "03-业务知识库",
    "04": "04-内容方法库",
    "05": "05-IP-Profile",
    "06": "06-Agent与Workflow",
    "07": "07-生产与反馈",
    "AGENTS": "AGENTS",
    "README": "README",
}


def root_content(binding: Binding, key: str) -> str:
    if key == "AGENTS":
        return (
            f"# {binding.knowledge_base_name} 运行规则\n\n"
            f"主体类型：{binding.subject_type}；主后端：{binding.backend_type}。\n\n"
            "每次先读 AGENTS，再读 06 和目标目录。01 保存通过 Gate 的原件与来源；02 只放真实异常；"
            "03 保存业务知识；04 保存内容方法；05 保存确认的 Profile；06 只绑定真实已验证 Skill；07 保存生产与反馈。"
            "资料入库只走 zsk-router；原件先进入 01。未经单独授权，不生产、发布、发送或改变权限。"
            "客户编辑后的规则优先，项目升级不得静默覆盖。\n"
        )
    if key == "README":
        return (
            f"# {binding.knowledge_base_name}\n\n"
            "这是客户的主知识库。首次通过 WorkBuddy 说“创建知识库”；日常资料说“把资料入库”。\n\n"
            "01—07 分别保存来源、待审核、业务知识、内容方法、Profile、已验证工作流与生产反馈。"
            "隐私、权限或归属不清的资料会停在 02。AGENTS、README 和 06 可由客户修改。"
            "保存、生产与发布是不同动作；遇到问题请提供后端、目标位置和报错原因。\n"
        )
    if key == "06":
        return (
            "# Agent 与 Workflow\n\n"
            "| 工作名称 | 明确调用 | 入口说法 | 读取范围 | 输出位置 | 人工确认点 | 当前状态 | 最后核验 |\n"
            "|-|-|-|-|-|-|-|-|\n"
            "| 待核验工作流 | 未登记 | 未登记 | 未登记 | 未登记 | 启动前确认 | unavailable | 未核验 |\n\n"
            "只有已安装并真实验证的 Skill/Workflow 才能改为 active；不得按名字模拟执行。\n"
        )
    return ""


def root_object_kind(binding: Binding, key: str) -> str:
    """返回当前后端的九个逻辑根对象的物理类型。"""
    if binding.backend_type == "obsidian" and key not in {"AGENTS", "README"}:
        return "directory"
    return "markdown_file"


def root_payload_fingerprint(binding: Binding, key: str, content: str) -> str:
    material = "\n".join(("root", key, binding.client_id, binding.template_version, content)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def template_fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def template_preview(binding: Binding) -> Mapping[str, object]:
    obsidian = binding.backend_type == "obsidian"
    return {
        "template_version": binding.template_version,
        "root_titles": {key: ROOT_TITLES[key] for key in ROOT_KEYS},
        "root_object_kinds": {key: root_object_kind(binding, key) for key in ROOT_KEYS},
        "summaries": {
            "AGENTS": "先读规则、资料只走 zsk-router、客户规则优先。",
            "README": "创建、日常入库、01—07、异常和保存/生产/发布边界。",
            "06": "06 是目录；不创建飞书工作流表格。" if obsidian else "只列已验证工作流；未验证项明确 unavailable。",
        },
        "contract_fields": {
            "AGENTS": ("01—07职责", "入库只走 zsk-router", "原件先进入 01", "02仅异常", "06真实已验证Skill", "无授权不生产或发布"),
        } | ({} if obsidian else {"06": ("工作名称", "明确调用", "入口说法", "读取范围", "输出位置", "人工确认点", "当前状态", "最后核验")}),
    }
