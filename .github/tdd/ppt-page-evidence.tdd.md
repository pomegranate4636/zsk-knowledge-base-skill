# PPT 页级文字证据与飞书来源页 TDD 证据

## 来源与用户旅程

本轮没有外部计划文件，验收目标直接来自用户确认的八项增强：

1. 作为资料入库用户，我希望 PPT 原生文字与图片页 OCR 按真实页码形成可核验证据，以便知识卡可以追溯到原页。
2. 作为审核者，我希望低置信 OCR 在校对前停止，以免错误文字成为正式知识。
3. 作为知识库使用者，我希望每条知识事实携带页码、逐字原文和证据 SHA256，以免概括内容失去来源约束。
4. 作为飞书知识库发布者，我希望来源 Docx 同时包含校对正文和高清原页图，并在写后核对正文、图片数量、尺寸和下载媒体 SHA256。

## RED / GREEN 检查点

| 阶段 | RED 证据 | GREEN 证据 | 检查点 |
|---|---|---|---|
| 页文字与知识卡合同 | 新测试因缺少 `PageTextEvidence`、`KnowledgeFact` 和页文字模块而 ImportError | `tests.test_page_text_evidence` 与 `tests.test_knowledge_page_evidence` 7 项通过；相关核心组合 30 项通过 | RED `9f6e174`; GREEN `7ce1001` |
| 飞书来源页媒体回读 | `RecordedCliCall` 不支持 `download_payload`，证明原实现无法下载远端媒体验哈 | 飞书来源页、页证据、知识卡组合 33 项通过 | RED `0dff467`, `b57e717`; GREEN `4d21891` |
| 安装合同 | 缺少 `local_ocr_status`，且缺 `ocr_provider.py` 时仍误判 shared 完整 | `tests.test_install_doctor` 13 项通过；Windows 标准安装路径测试通过 | RED `af2d3d4`, `d6ffb97`; GREEN `9d94ea1` |
| 用户级中文 OCR 数据 | 默认安装只有 `eng`/`osd`，用户级语言目录未传给 Tesseract | Provider 与 doctor 均显式识别用户级 tessdata；真实 OCR 冒烟结果置信度 0.911 | RED：本轮未提交测试；GREEN：本轮收尾提交 |

## 可执行保证

| # | 保证 | 测试 | 类型 | 结果 |
|---|---|---|---|---|
| 1 | PPT 原生文字按幻灯片顺序提取，图片页可识别 | `test_extracts_native_text_and_detects_image_only_page` | 单元 | PASS |
| 2 | 只有图片页调用本地 OCR | `test_only_image_page_uses_local_ocr` | 单元 | PASS |
| 3 | 低置信 OCR 未校对时停止 | `test_low_confidence_ocr_stops_without_review` | 单元 | PASS |
| 4 | 校对正文与页图共同生成稳定证据 SHA256 | `test_reviewed_correction_is_hashed_with_page_image` | 单元 | PASS |
| 5 | 本地 Tesseract TSV 结果产生加权置信度，超时、空输入、非零退出均 fail closed | `test_tesseract_provider_parses_local_tsv_confidence`, `test_tesseract_provider_fails_closed` | 单元 | PASS |
| 6 | 完整页证据知识卡缺页码、原文或证据哈希时停止 | `test_page_evidence_source_rejects_unbound_fact_text`, `test_fact_must_match_page_verbatim_and_hash` | 集成 | PASS |
| 7 | 飞书来源页使用 Docx 内嵌媒体，不再把原页图写成 01 同级 Drive 文件 | `test_feishu_page_is_not_uploaded_as_a_sibling_drive_file` | 集成 | PASS |
| 8 | 飞书写后回读图片数量、尺寸，并下载媒体重算 SHA256 | `test_embeds_page_and_reads_back_count_dimensions_and_hash` | 录制 E2E | PASS |
| 9 | 正文篡改和远端媒体哈希不一致均返回 `readback_failed` | `test_readable_body_tampering_is_not_hidden_by_unchanged_hash_markers`, `test_remote_media_hash_mismatch_fails_closed` | 录制 E2E | PASS |
| 10 | 安装器要求页文字/OCR 模块并识别 Windows 标准 Tesseract 路径 | `test_shared_requires_page_text_and_local_ocr_modules`, `test_windows_standard_tesseract_path_works_before_path_refresh` | 单元 | PASS |
| 11 | Provider 和 doctor 使用含中英文模型及 TSV 配置的用户级 tessdata | `test_provider_uses_user_level_tessdata_directory`, `test_doctor_uses_user_level_tessdata_directory` | 单元+真实冒烟 | PASS |

## 回归、覆盖率与已知环境限制

- 定向回归：本轮各 RED/GREEN 组均通过；`compileall` 通过。
- 全量发现：66 项；排除当前 Windows 账号无法创建符号链接的既有用例后，65 项通过。被排除用例失败原因为 `WinError 1314`，不是断言失败。
- 标准库 `trace`：`shared.page_text` 84%，`shared.ocr_provider` 89%，`shared.stage6_knowledge` 87%。飞书与 Stage5 文件包含大量既有平台/后端分支，文件整体覆盖率低于 80%，但本轮新增的写入、正文篡改、图片数量、尺寸、下载哈希和失败阻断路径均有直接测试。
- 未执行真实飞书写入；真实隔离 Wiki 目标、测试原件和清理方案仍需单独确认。
