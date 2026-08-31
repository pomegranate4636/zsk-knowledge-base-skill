---
name: zsk-profile
description: 将已登记的 profile_material 整理为 05 的单一主 Profile，分开确认事实、运营设定和候选素材；只由 zsk-router 后台调用。
---

# ZSK Profile

## 职责

保持三层边界：主体确认事实、当前项目运营设定、员工与 AI 候选素材。每个绑定只允许一份 active primary；候选素材不能升级为事实。

## 阶段 8 边界

本阶段只接收已登记、处理权明确且隐私状态为 `passed` 或 `redacted` 的来源中，被语义识别为主体资料的内容单元；来源整体可以是 `unknown` 或 `mixed`。先将内容单元中的主体与当前 Binding 的主体逐字核对，再按 06 偏好写入候选区或更新包含三个独立区块的主 Profile 并回读。

重复提交同一主 Profile 只能回读复用；同一 Binding 已有不同的 active primary 时返回 `version_conflict`，不得覆盖、迁移或合并旧 Profile。

## 必须遵守

- 来源自身必须能说明目标主体；不能因为文件名相似、已有 Profile 或上下文猜测而合并。
- 来源不足、来源未登记、主体不匹配或三层内容不完整时停止，不写 05。
- 写入由当前绑定的飞书或 Obsidian Adapter 执行；不读取真实 Profile，不执行迁移或合并。
