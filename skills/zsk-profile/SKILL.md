---
name: zsk-profile
description: 将已登记的 profile_material 整理为 05 的可选择 Profile，分开确认事实、运营设定和候选素材；支持同库多 IP，primary 仅为默认；只由 zsk-router 后台调用。
---

# ZSK Profile

## 职责

保持三层边界：主体确认事实、当前项目运营设定、员工与 AI 候选素材。一个知识库可以有多个 active Profile，但最多一个 active primary；候选素材不能升级为事实。

## 阶段 8 边界

本阶段只接收已登记、处理权明确且隐私状态为 `passed` 或 `redacted` 的来源中，被语义识别为主体资料的内容单元；来源整体可以是 `unknown` 或 `mixed`。主体必须由来源自身证明，但人物名不必等于客户名。写入后同步 Profile 索引并回读。

`profile_id` 由客户与具体人物共同确定。重复提交同一 Profile 只能回读复用；新增非 primary Profile 不影响现有默认，新增第二个 active primary 时返回 `version_conflict`。

## 必须遵守

- 来源自身必须能说明目标主体；不能因为文件名相似、已有 Profile 或上下文猜测而合并。
- 来源不足、来源未登记、主体不匹配或三层内容不完整时停止，不写 05。
- 写入由当前绑定的飞书或 Obsidian Adapter 执行；不读取真实 Profile，不执行迁移或合并。
