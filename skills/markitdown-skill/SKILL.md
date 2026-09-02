---
name: markitdown-skill
description: 将 PDF、Word、PowerPoint、Excel、HTML 和 JSON 转为 Markdown 的 Microsoft MarkItDown 配套 Skill。供 ZSK 后台资料入库及独立文档转换使用；不负责知识库路由、分类、保存或发布。
metadata:
  short-description: 文档转 Markdown 的 ZSK 必装配套能力
  requires:
    bins:
      - markitdown
---

# MarkItDown

使用本机 Microsoft MarkItDown CLI 生成可读 Markdown。它是 ZSK 的必装配套能力，但客户资料入库仍只从 `zsk-router` 进入。

## ZSK 使用边界

- ZSK 对 DOCX、PPTX、XLSX、PDF、HTML、JSON 的正式可读版只使用 MarkItDown；MD、TXT、CSV 走轻量本地规范化。
- 只做本地文字转换，不启用插件、Azure Document Intelligence、外部 URL、图片 OCR、音视频转写或 LLM 图片描述。
- 转换结果为空、损坏或转换器不可用时，ZSK 只写 02 的安全异常，不保存原件或正文。
- 不把 Markdown 转换结果直接当业务事实；后续 03、04、05 仍必须走来源、隐私和路由 Gate。
- PPTX 中 MarkItDown 生成的页码注释会规范为可见的 `## 第 N 页`，方便回链原页。
- MarkItDown 只写了 `![](图片N.jpg)` 但没有真实输出图片时，ZSK 必须移除该失效链接并明确说明视觉未保存；不得把破图占位写进客户知识库。

## 独立转换

```bash
markitdown document.pdf -o readable.md
```

运行 `python3 install.py --doctor` 检查 ZSK 组件和 MarkItDown。若需要补齐最小转换依赖，运行：

```bash
python3 install.py --install-markitdown
```

ZSK 可在页面视觉影响含义或客户已配置保留完整页面时，为 PDF/PPTX 启用独立的完整页证据模式。当前绑定中客户主动提交的文件默认允许处理与保留原件。该模式由 shared 页渲染器、PPT 原生文字提取器和多次本地 OCR 一致性验证负责，不改变 MarkItDown 的文字转换职责；Windows 或 macOS 检测到 Microsoft PowerPoint 时优先使用其原生导出，LibreOffice 仅作为无原生后端时的备用。OCR 只处理页图，不联网；无法自动可靠还原时整份资料零写入，自动图片描述和猜测式图文映射仍不在范围内。
