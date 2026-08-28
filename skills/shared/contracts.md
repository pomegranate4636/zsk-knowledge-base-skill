# ZSK 共享合同

这是五个 Skill 共用的后端中立合同。它只规定稳定身份、状态、准入结果和不透明对象引用；真实后端的 token、节点类型、文件路径和 API 字段不能进入这里。

## 核心对象

- `Binding`：`zsk-client-binding-v1`、稳定 `client_id`、主体类型、主后端类型、后端定位符、九个逻辑根对象引用和模板版本。一个 `client_id` 或后端定位符冲突时零写入。
- `BindingRegistry`：`zsk-registry-v1` 的进程内 Registry；Fake Adapter 首次成功解析后锁定单一绑定，后续不同主体、客户或定位符不切换上下文。
- `SourceRecord`：内容寻址 `source_id`、客户绑定、标题、来源角色、内容类型、原件/可读版 SHA256、隐私状态、权限状态、原件保存授权、版本关系、处理状态和后端对象引用。
- `PrivacyDecision`：`zsk-privacy-v1`、隐私状态、权限状态和安全说明；安全说明不替代敏感正文。
- `RouteDecision`：`03`、`04`、`05`、`indexed_only` 或 `02`，附人能看懂的原因和可选原因码。不使用数字评分。
- `BackendObjectRef`：稳定对象 ID、对象类别、对上层不透明的定位符和版本。上层不能解析定位符来猜后端。
- `AdapterResult`：`ok`、`reused`、`blocked` 或 `failed`，附统一原因码、检查项、稳定引用和可回读信息。

## Adapter 的 12 个方法

`doctor`、`resolve_binding`、`inspect_structure`、`create_skeleton`、`read_rules`、`store_original`、`store_readable`、`write_exception`、`write_knowledge_asset`、`write_method_asset`、`write_profile`、`read_back`。

每个方法都返回后端中立的 `AdapterResult`。阶段 1 的 Fake Adapter 只在内存中运行；真实后端连接和读写属于后续阶段。

## 写入不变量

1. 绑定先解析，未解析或绑定不一致时停止。
2. 骨架为九个逻辑根对象，create-only；完整重复执行返回 `reused`，部分结构返回 `structure_conflict`。
3. 阶段 5 的 `source_id` 为 `SRC-`＋原件 SHA256 前 24 位，完整哈希复核碰撞；同字节改名复用，同字节但归属或角色冲突进入 `duplicate_conflict`。
4. 正式资产必须先看到当前绑定下同时完成原件与可读版登记的 `source_id`；不能仅凭调用方传入的字符串写入。
5. 异常记录保存稳定异常 ID、原因码、安全说明、待判断问题、`source_id` 和必要的非敏感来源对象引用，不保存敏感正文。
6. 正式资产必须有来源回链；资产 ID 冲突不覆盖原对象。
7. 每次写入之后都要以稳定引用执行 `read_back`；回读必须核对绑定、object_id、locator、object_kind、version 和可重算 fingerprint，失败不能报告成功。
8. 敏感原件必须先取得明确保存授权；阶段 5 Evidence 只保存安全摘要和计数，不保存用户原话、文件名、绝对路径或正文。
