# ZSK 共享合同

这是五个 Skill 共用的后端中立合同。它只规定稳定身份、状态、准入结果和不透明对象引用；真实后端的 token、节点类型、文件路径和 API 字段不能进入这里。

## 核心对象

- `Binding`：`zsk-client-binding-v1`、稳定 `client_id`、主体类型、主后端类型、后端定位符、九个逻辑根对象引用和模板版本。一个 `client_id` 或后端定位符冲突时零写入。
- `BindingRegistry`：`zsk-registry-v1` 的进程内 Registry；Fake Adapter 首次成功解析后锁定单一绑定，后续不同主体、客户或定位符不切换上下文。
- `SourceRecord`：内容寻址 `source_id`、客户绑定、人类可读展示名、标题、中性来源角色、内容类型、原件/可读版 SHA256、隐私状态、权限状态、原件保存授权、版本关系、处理状态和后端对象引用；需要完整页证据时，还保存页数、连续页图清单和连续页文字证据。
- `PageArtifact`：完整页面证据，保存 `source_id`、页码、稳定文件名、像素尺寸和页图 SHA256。
- `PageTextEvidence`：同页原生文字、本地 OCR、自动验证正文、置信度、审核状态和绑定页图 SHA256 的证据哈希；未通过自动验证时不得暴露为已核验原文，来源登记流程会零写入停止。
- `KnowledgeFact`：知识卡中的单条事实、来源页码、逐字原文和页文字证据 SHA256；四者必须共同出现并通过 SourceRecord 校验。
- `PrivacyDecision`：`zsk-privacy-v1`、隐私状态、权限状态和安全说明；安全说明不替代敏感正文。
- `RouteDecision`：`03`、`04`、`05`、`indexed_only` 或 `02`，附人能看懂的原因和可选原因码。不使用数字评分。
- `BackendObjectRef`：稳定对象 ID、对象类别、对上层不透明的定位符和版本。上层不能解析定位符来猜后端。
- `AdapterResult`：`ok`、`reused`、`blocked` 或 `failed`，附统一原因码、检查项、稳定引用和可回读信息。

## Adapter 的 13 个方法

`doctor`、`resolve_binding`、`inspect_structure`、`create_skeleton`、`read_rules`、`store_original`、`store_readable`、`store_page_evidence`、`write_exception`、`write_knowledge_asset`、`write_method_asset`、`write_profile`、`read_back`。

每个方法都返回后端中立的 `AdapterResult`。阶段 1 的 Fake Adapter 只在内存中运行；真实后端连接和读写属于后续阶段。

## 写入不变量

1. 绑定先解析，未解析或绑定不一致时停止。
2. 骨架为九个逻辑根对象，create-only；完整重复执行返回 `reused`，部分结构返回 `structure_conflict`。
3. 阶段 5 的 `source_id` 为 `SRC-`＋原件 SHA256 前 24 位，完整哈希复核碰撞；它只进入元数据，不作为客户可见文件名。同字节改名复用，同字节但客户归属冲突进入 `duplicate_conflict`；03/04/05 角色由登记后的内容单元语义路由决定。
4. 正式资产必须先看到当前绑定下同时完成原件与可读版登记的 `source_id`；不能仅凭调用方传入的字符串写入。
5. 异常记录保存稳定异常 ID、原因码、安全说明、待判断问题、`source_id` 和必要的非敏感来源对象引用，不保存敏感正文。
6. 正式资产必须有来源回链；资产 ID 冲突不覆盖原对象。
7. 每次写入之后都要以稳定引用执行 `read_back`；回读必须核对绑定、object_id、locator、object_kind、version 和可重算 fingerprint，失败不能报告成功。
8. 敏感原件必须由 06 项目级偏好或本次确认明确允许保存；阶段 5 Evidence 只保存安全摘要和计数，不保存用户原话、文件名、绝对路径或正文。OCR 与原生页文字必须在后端写入前再次执行隐私检查和脱敏。
9. 完整页证据默认关闭。只有 PDF/PPTX 的视觉版式、图表或截图本身影响资料含义，并且客户明确允许保存完整原件时，才能启用 `required`；渲染前必须完成权限和隐私 Gate。
10. 页级证据必须使用每个来源独立的临时目录，核对真实总页数、连续页码和每页 SHA256；任一页缺失、重复、错序或回读失败都不能报告登记成功。
11. PPTX 先按真实幻灯片顺序提取原生文字；图片、图表、SmartArt 或 PDF 视觉对象可能承载额外信息时才调用本地 OCR Provider。OCR 不联网、不调用托管服务；至少两次结果未高度一致、引擎缺失或执行失败时停止。
12. 完整页证据来源生成 03 知识卡时，每条事实必须绑定同页的原文摘录和 `evidence_sha256`；摘录必须真实存在于该页脱敏正文，字符串概括不能绕过证据合同。
13. 飞书来源页必须在同一 Docx 内包含校对正文与高清原页图。写后以 full 模式回读图片块，核对数量和显示尺寸，再下载媒体重算 SHA256；正文或任一媒体不一致均返回 `readback_failed`。
