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
- 对 PPTX，若 MarkItDown 输出独立的 `<!-- Slide number: N -->`，必须在可读版中规范为可见的 `## 第 N 页`；不得把这类注释原样展示给客户，也不得将幻灯片内标题误作 01 来源页标题。

## 独立转换

```bash
markitdown document.pdf -o readable.md
```

运行 `python3 install.py --doctor` 检查 ZSK 组件和 MarkItDown。若需要补齐最小转换依赖，运行：

```bash
python3 install.py --install-markitdown
```

## 房产 PPT 的页面视觉保留

房地产资料进入来源页时，整页幻灯片本身是必需来源证据：必须逐页渲染为完整页面图片，并与 `第 N 页` 一一对应保留在 01 来源页。不得用 PPT 内嵌图片的拆分提取替代整页幻灯片，也不得遗漏、重排或以文字转换替代页面视觉。

这项规则不要求 OCR、图片描述或自动推断图文关系。运行环境不能可靠渲染、计数并回读全部 PPT 页面时，必须停止并说明富媒体转换不可用；不得静默降级成只有文字的来源页。
