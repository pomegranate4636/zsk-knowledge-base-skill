---
name: zsk-ruku
description: 通用知识库的来源登记职责。负责来源身份、可读性、隐私与权限状态、版本防重和 01/02 结果；只由 zsk-router 后台调用。
---

# ZSK Ruku

## 职责

把一次来源处理变成稳定、可解释、可回读的来源记录。原件和可读版分开登记，正式资产只能通过来源身份与来源回链继续流转。

## 统一 Markdown 转换

MD、TXT 原样规范化，CSV 严格转 Markdown 表格；JSON、HTML/HTM、DOCX、PPTX、XLSX、PDF 一律由本机 Microsoft MarkItDown 转为唯一正式 `readable.md`。转换不联网、不调用大模型；MarkItDown 不可运行、输出为空或转换失败时，只写 02 的安全说明，不保存原件或正文。旧版二进制 Office（.doc/.xls/.ppt）、图片、音视频仍准确停止。

`source_id` 使用原件 SHA256；通过权限与隐私 Gate 后，在 01 create-only 分开保存保留安全扩展名的原件和带来源记录的可读 Markdown。可读版必须记录转换器名称和版本。异常只在 02 保存固定安全说明。只返回 `registered / reused / exception`，不判断 03/04/05。

## 通用完整页证据

PDF/PPTX 默认不生成页图。只有 Router 明确传入 `page_evidence_mode=required` 时才启用；启用前必须取得原件永久保留授权。页图不代替 MarkItDown 文本，两者共同进入 01：

- 每个来源在私有临时目录中渲染，结束后自动清理临时文件。
- 使用 PDF 的真实总页数核对完整页集；页码必须从 1 连续到总页数。
- `readable.md` frontmatter 持久保存渲染器、总页数、每页文件名和 SHA256。
- Obsidian 按 `source_id/pages/` 隔离；飞书文件名同时包含 `source_id`、页码和页图哈希。
- 任一依赖缺失、缺页、重复、错序、写入或回读失败时进入 02，不能报告登记成功。

当前页级证据只保存完整页图，不做 OCR、图片描述、图片检索、自动图文匹配或行业分类。PPTX 在 macOS 有 Microsoft PowerPoint 时优先走原生 PowerPoint 导出并记录渲染器；否则使用 LibreOffice。原生渲染器已存在但执行失败时停止，不静默降级。

## 停止条件

来源不可读、权限未知、隐私未决、版本关系冲突、重复来源归属冲突或写后回读失败时，只返回统一原因码和无敏感异常引用，不猜测正文，不静默换客户或后端。

敏感原件只有客户明确允许私有保存时才能进入 01；未授权时 01、02 和 Evidence 均不得出现原件引用或敏感正文。
