---
name: zsk-profile
description: 将已登记的 profile_material 整理为 05 的单一主 Profile，分开确认事实、运营设定和候选素材；只由 zsk-router 后台调用。
---

# ZSK Profile

## 职责

保持三层边界：主体确认事实、当前项目运营设定、员工与 AI 候选素材。每个绑定只允许一份 active primary；候选素材不能升级为事实。

## 阶段 8 边界

本阶段只接收已登记、处理权明确且隐私状态为 `passed` 或 `redacted` 的 `profile_material` 来源。先将来源主体与当前 Binding 的主体逐字核对，再在 05 写入一份包含三个独立区块的主 Profile 并回读。

重复提交同一主 Profile 只能回读复用；同一 Binding 已有不同的 active primary 时返回 `version_conflict`，不得覆盖、迁移或合并旧 Profile。

## 豪宅策略 `haozhai-v1`

豪宅绑定必须通过 `shared/haozhai_policy.py` 构造 Stage 8 请求。三层固定为“本人确认事实、项目运营设定、员工与 AI 候选素材”，三层都不能为空、不能互相升级；每一条必须逐字存在于当前有效来源。

“本人确认事实”比普通来源约束多一道独立复核：文字 SHA256 必须出现在已审核确认摘录集合中，否则返回 `profile_confirmation_missing`。来源主体必须与 Binding 客户名逐字一致。PDF/PPTX 条目还必须绑定真实页码和 OCR 哈希，正式 Profile 来源区保留页面图片回链。

## 必须遵守

- 来源自身必须能说明目标主体；不能因为文件名相似、已有 Profile 或上下文猜测而合并。
- 来源不足、来源未登记、主体不匹配或三层内容不完整时停止，不写 05。
- 来源逐字校验、本人事实复核或页面图片回链任一失败时，不得展示成可确认的正式草稿。
- 写入由当前绑定的飞书或 Obsidian Adapter 执行；不读取真实 Profile，不执行迁移或合并。
