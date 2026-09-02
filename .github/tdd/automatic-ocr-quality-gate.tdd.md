# 自动 OCR 质量 Gate TDD 证据

## 来源与用户旅程

本轮没有外部计划文件。需求来自已确认的产品决策：当前绑定中客户主动提交的文件默认可处理和保留原件；客户不参与 OCR 校对；完整页资料只能在全部页面经自动验证后才可入库。

| 用户旅程 | 保证 |
| --- | --- |
| 客户提交普通资料 | 默认请求具有 `allowed` 处理状态和原件保留授权。 |
| 客户提交图片型 PDF/PPTX | 系统以 300 DPI 渲染，并用三次本地 OCR 的一致性验证文字。 |
| 客户提交带真实文字层的 PDF | 系统优先逐页使用有效内嵌文字；模板占位或空白文字不作为可信正文。 |
| 自动验证不能可靠还原任一页 | 返回 `file_quality_insufficient`，不写 01、02、03、04 或 05。 |

## RED / GREEN 检查点

| 阶段 | RED 证据 | GREEN 证据 | 检查点 |
| --- | --- | --- | --- |
| 自动质量 Gate | 新测试导入 `AutoOcrProvider` 失败；默认原件保留断言失败；低置信页仍返回旧的 `privacy_approval_required`/校对路径。 | `py -X utf8 -m unittest tests.test_page_text_evidence tests.test_page_evidence`：31 项通过。 | RED `69acf9e`; GREEN `fix: enforce automatic OCR quality gate`（本提交） |
| PDF 原生文字优先 | 新增测试因 `extract_pdf_page_text` 不存在而失败。 | `py -X utf8 -m unittest tests.test_page_text_evidence tests.test_page_evidence`：33 项通过。 | RED `test: cover PDF native text intake`；GREEN `fix: prefer PDF native text evidence` |

## 实现与验证

- `AutoOcrProvider` 使用 Tesseract 的 3、6、11 三种页面分割模式；优先要求两次结果的规范化文字相似度达到 0.92。版式差异导致这一条件不成立时，只有主结果置信度至少 0.85、且另一结果相似度至少 0.65 才自动接受主结果；无印证结果仍返回零置信度。
- 完整页模式以 300 DPI 渲染；低质量页不再请求或接受客户校对。阶段 5 在任何后端写入之前返回 `file_quality_insufficient`。
- `py -X utf8 -m compileall -q skills` 通过。
- `git diff --check` 通过。
- 全量 `py -X utf8 -m unittest discover -s tests -p "test_*.py"` 执行 74 项，其中 73 项通过；仅 `test_symlink_vault_is_rejected` 因 Windows 当前账户缺少创建符号链接特权失败，与本轮 OCR 改动无关。

## 覆盖与已知边界

定向测试覆盖默认授权、同文多次 OCR 通过、不同文 OCR 拒绝、低置信整份资料零写入和既有页证据路径。未安装额外的本地布局 OCR 引擎；当前实现只使用已验证可用的 Tesseract 多次识别，因此对极低清晰度扫描件仍会正确零写入，而不会伪造高质量文字。
