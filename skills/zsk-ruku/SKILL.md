---
name: zsk-ruku
description: 通用知识库的来源登记职责。负责来源身份、可读性、隐私与权限状态、版本防重和 01/02 结果；只由 zsk-router 后台调用。
---

# ZSK Ruku

## 职责

把一次来源处理变成稳定、可解释、可回读的来源记录。原件和可读版分开登记，正式资产只能通过来源身份与来源回链继续流转。

## 阶段 5 Slim

确定性读取 MD、TXT、严格 CSV、JSON、HTML/HTM，以及 DOCX、PPTX、XLSX（OOXML 按 ZIP+XML 规范位置提取），不安装依赖、不调用大模型。PDF 为原则支持：依赖宿主环境提供的可选依赖 `pypdf`，缺失时按 `format_unsupported` 准确停止。旧版二进制 Office（.doc/.xls/.ppt）、图片、音视频仍准确停止。`source_id` 使用原件 SHA256；通过权限与隐私 Gate 后，在 01 create-only 分开保存保留安全扩展名的原件和带来源记录的可读 Markdown。异常只在 02 保存固定安全说明。只返回 `registered / reused / exception`，不判断 03/04/05。

## 停止条件

来源不可读、权限未知、隐私未决、版本关系冲突、重复来源归属冲突或写后回读失败时，只返回统一原因码和无敏感异常引用，不猜测正文，不静默换客户或后端。

敏感原件只有客户明确允许私有保存时才能进入 01；未授权时 01、02 和 Evidence 均不得出现原件引用或敏感正文。
