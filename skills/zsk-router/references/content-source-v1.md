# content-source-v1 公共合同

ZSK 是 Schema 规范源；Content 产品携带兼容校验副本，但运行时不得导入 ZSK 代码。

## 三个对象

- 宿主 Registry：`~/.codex/.content-workflows/knowledge-base-registry.json`
- 知识库 Manifest：`06-Agent与Workflow/content-source-manifest.json`
- Profile 索引：`06-Agent与Workflow/content-profile-index.json`

飞书在 06 下使用同名文档，并在 Registry/Manifest 中保存稳定对象引用；不得保存 token、cookie 或其他凭据。

## 绑定与选择

Registry 以 `binding_id` 为键，分别保存 `client_id`、`knowledge_base_id`、后端、定位符、Manifest/Profile 索引引用、支持工作流和工作流默认值。同一客户可有多个知识库。

一个知识库可有多个 active Profile，最多一个 primary。IP 选择顺序：本次明确指定、工作流默认、primary、唯一 active；仍不唯一就停下要求用户选择。每个工作流默认值同时保存 `profile_id` 与 `use_no_ip`；两者不能同时启用。`none` 只能由本次明确指定或 `use_no_ip=true` 的已确认配置提供。

## 写入边界

新建知识库自动 create-only 生成基础 Manifest 和空 Profile 索引。宿主 Registry 必须先展示 `wrote=false` 的完整预览，再用与当前预览匹配的真人确认写入；确认后采用原子替换并回读。现有知识库只在独立迁移确认后补清单，不自动覆盖。

Content 产品负责自己的配置器。ZSK 只提供合同和预览信息，不导入或执行下游产品代码。
