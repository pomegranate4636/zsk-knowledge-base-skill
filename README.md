# ZSK Knowledge Base Skill

一套面向豆包工作 / Codex / WorkBuddy 的通用知识库 Skill 组合。公开入口只有 `zsk-router`，其余组件负责资料登记、业务知识、参考方法和主体资料的安全处理。

## 仓库定位

这是 **ZSK 的唯一更新真源**。所有 ZSK 功能、安装说明和测试先在这里修改、验证并合并到 `main`。

ZSK 与下游仓库保持独立。ZSK 的代码只能从这里单向进入客户交付包和本机安装副本：

```text
zsk-knowledge-base-skill（唯一真源）
→ zhihui-mianmian-skills（客户交付包）
→ 客户或讲师本机 ~/.codex/skills（运行安装副本）
```

Content 口播 Slim 与 Content 公众号 Slim 都是独立的知识库消费仓库，只通过 `content-source-v1` 文件合同衔接，不复制或导入 ZSK 运行代码。不要在 Content 仓库或已安装副本中反向修改 ZSK；它们不是更新真源。

03 事实卡、04 内容资产和 05 Profile 都带稳定 ID、状态、适用范围与来源回链。一个知识库可以有多个 active Profile，但最多一个 primary；primary 只是默认 IP，不限制用户在口播或公众号任务中选择其他 IP。

## 让一段想法也有资料可用

04 同时保存同行完整内容拆解和结构方法：同行提供问题、观点、场景与细节，结构方法提供选择条件、段落作用和组合方式。ZSK 入库时保留这些内容价值，让下游 Content 工作流在用户没给对标时也能选材、组织和提出方向。客户无需先学习结构名称。

Stage 7 新增 `PeerContentRequest` 七段完整拆解和 `ContentMethodRequest` 多段结构/选择方法接口，继续使用已有 `peer_content_asset` / `content_method_asset` 文件合同。旧 `MethodRequest` 短方法卡及旧知识库保持可用；不自动覆盖已有卡片。详细字段与语义验收见 [04 内容保留与调用合同](skills/zsk-duibiao/references/rich-content-assets.md)。

保留同行细节不代表把同行经历变成客户经历。新卡标记来源归属，具体客户事实仍需 03/05 或本次确认材料支持；原件、权限、隐私、绑定、create-only 保存和回读要求不变。入库回执应说明哪些内容和方法已可供参考、还有什么资料缺口。ZSK 独立安装仍可完成入库，不依赖任何 Content 代码。

维护者可验证真实 Stage 5 → 富内容 Stage 7 → 两个 Content 独立配置器和无对标入口：

```bash
python3 -B tools/verify_rich_content_handoff.py \
  --content-koubo-slim-root /本机/content-koubo-slim \
  --content-gzh-slim-root /本机/content-gzh-slim
```

该工具只用合成资料和临时 Obsidian，检查七段同行拆解、结构末尾、来源限制、角色预算与共享绑定，止于待分析输入/Run 创建；不读取客户库、不批准人工 Gate、不生成或发布成稿。跨仓调用仅存在于验收工具，产品运行代码仍独立。

## 与 Content 工作流配合

新建知识库默认在 `04-内容方法库/口播结构` 带入完整口播结构库：22 份预置文档，覆盖方法论、基础结构、增强机制、时长适配和验证反馈。Obsidian 保存 Markdown 副本，飞书创建对应层级的文档并转换内部链接；预置内容与基础清单全部回读后才报告建库成功。06 中记录预置包版本，各客户可独立维护；公共模板更新不会自动改动已有库。此功能不改变 Content 的拆解或写作逻辑。

ZSK、口播、公众号三套仓库保持独立，推荐顺序是：

```text
先安装 ZSK
→ 创建并确认一个 Obsidian 本地知识库
→ 自动生成基础 Manifest 与 Profile 索引
→ 首次确认一次 Content 工作流连接
→ 安装口播或公众号 Slim
→ 第一次内容任务读取已确认的默认知识库
```

这是推荐交付顺序，不是靠安装先后自动识别。真正决定能否自动锁定资料库的，是公共 Registry 已存在、可回读，并且当前工作流能唯一解析 binding 与默认 IP。

新建知识库会在 06 中 create-only 生成 `content-source-manifest.json` 与 `content-profile-index.json`。它们只描述知识库本身，不代表已安装或绑定某个 Content 产品。连接工作流时先做零写入预览，真人确认后才登记宿主公共 Registry：`~/.codex/.content-workflows/knowledge-base-registry.json`。其他宿主必须使用自己的真实持久位置，不能照抄 Codex 路径。

旧口播 `.content-koubo-slim/client-registry.json`、v2 Manifest 和旧 Run 不会被覆盖或删除；只有旧配置时仍按旧规则运行。公共 Registry 与旧 Registry 同时存在但指向不一致时，口播会停止并要求人工确认。

Registry 以 `binding_id` 区分客户、知识库和后端，同一客户可以有多个知识库。IP 解析顺序为：本次明确指定 → 工作流默认 → primary → 唯一 active → 要求选择。重名别名、多知识库无默认、软链接或回读失败都会停止。

Content 公众号 Slim 支持 Obsidian 与飞书；Content 口播 Slim 当前只支持 Obsidian，遇到飞书 binding 会明确停止，不会猜本地同步目录。

用户可以在 ZSK 建库完成后说：

> 请使用 zsk-router，把刚创建的 Obsidian 知识库设为 Content 口播 Slim 的默认内容资料库。先做零写入预检，给我看完整路径和讲述者模式，等我确认后再连接。

维护者可在两个仓库位于本机时运行全新隔离验收：

```bash
python3 tools/verify_content_koubo_slim_handoff.py \
  --content-koubo-slim-root /本机/content-koubo-slim
```

该验收会使用全新 Skills 目录、全新 Obsidian 知识库和全新 Run，不读取真实客户资料，也不会生成最终成稿。

维护者可同时验证公共合同、同库双 IP、公众号/口播共享 binding 与口播飞书阻断：

```bash
python3 tools/verify_content_source_v1.py \
  --content-koubo-slim-root /本机/content-koubo-slim \
  --content-gzh-slim-root /本机/content-gzh-slim
```

## 一句话理解

```text
客户上传资料
→ zsk-router
→ MarkItDown 转 readable.md
→ 01 来源登记
→ AI 按内容单元自动识别
→ 03 业务知识 / 04 内容方法 / 05 Profile；真实异常才进 02
```

客户始终只调用 `zsk-router`。`markitdown-skill` 是必装后台能力，不是第二个入库入口。

三者关系很简单：WorkBuddy 是执行工作的工具，ZSK 是告诉它如何建库和入库的 Skill，飞书知识库是最终保存资料的位置。安装 ZSK 不等于已经创建飞书知识库；必须先连接客户自己的飞书账号，再由客户确认创建或绑定哪个知识库。

对于页面版式、图表或截图本身影响含义的 PDF/PPTX，可以启用通用“完整页证据”。它保存高清原页图，按页提取 PPT 原生文字，并对图片页以 300 DPI 的多次本地 OCR 一致性自动验证；客户不需要逐页校对。任一页无法自动可靠还原时，整份资料不写入知识库，客户只需上传高清 PDF 或原始文件。它不包含任何行业专用规则，也不会默认增加到每次入库。

客户不需要手工选择资料应该放 03、04 还是 05。同一份资料可以拆出业务事实、通用方法和主体资料，分别入库；机器指纹只留在元数据中，客户看到的是“日期＋中文标题”的目录和文件名。分类拿不准时留在 01，不把普通分类问题塞进 02。

## 一个地址完成自助安装

把下面这句话发给豆包工作、Codex 或 WorkBuddy，不需要讲师分发文件：

> 请从 https://github.com/slbb1995/zsk-knowledge-base-skill 获取完整 ZSK 项目，先读 README，再安装到当前宿主已核实的持久 Skills 目录，不覆盖同名目录。检查六个 Skill、shared 和口播预置资源完整可加载。先复用宿主已有飞书能力，缺失时按 https://github.com/larksuite/cli 官方说明补齐；豆包已有账号绑定时不要重复安装或登录。现在只准备建库，暂不下载文档转换依赖；首次上传富文档时再检查并补齐所需格式。告诉我是否需要刷新技能或重新打开任务。

安装后发送：

> 请使用 zsk-router 检查当前是否具备创建知识库的条件，先检查，不要创建。

## 宿主和飞书连接

详细规则见 [宿主接入与按需依赖](skills/zsk-router/references/host-setup.md)。

- 豆包工作：优先复用内置 CLI 和本人飞书绑定；不因没有独立 `auth` 命令而要求重装。必须在豆包实际执行环境中核验。
- Codex、WorkBuddy 或其他平台：同样先检查已有能力，缺失才从 [飞书 CLI 官方仓库](https://github.com/larksuite/cli) 按当前安装说明补齐并授权。
- Obsidian 本地知识库：不要求安装或授权飞书。
- 账号已绑定只代表身份可验证，不保证创建空间、写入文档的权限；宿主未暴露 scopes 时如实显示“写权限尚未预验证”，后续真实调用由飞书逐项校验，失败停止并报告已有对象。
- 当前 Python 运行层使用可执行 `lark-cli`，不把仅有连接器名称当成兼容性通过。

## 手动安装与复测

Codex 默认目录：

```bash
git clone https://github.com/slbb1995/zsk-knowledge-base-skill.git
python3 zsk-knowledge-base-skill/install.py
python3 zsk-knowledge-base-skill/install.py --doctor
```

其他宿主显式指定已核实的持久目录（将示例路径替换为当前环境的真实路径）：

```bash
python3 zsk-knowledge-base-skill/install.py --host doubao --dest /已核实的持久Skills目录
python3 zsk-knowledge-base-skill/install.py --host doubao --dest /已核实的持久Skills目录 --doctor
```

`--host` 可选 `codex`、`workbuddy`、`doubao`、`other`。非 Codex 未提供 `--dest` 时停止，防止装到错误宿主。检查完整目录后，还需按宿主实际方式刷新技能或重开任务验证加载；只在当前任务读到 SKILL.md 不能证明重开后仍可用。

安装器不覆盖同名组件；发现冲突时先核对完整版本，再决定保留或更新。更新不可拼接不同版本的 shared 与入口。

## 文档转换按需准备

`markitdown-skill` 说明随 ZSK 分发；MarkItDown 程序及格式依赖按需准备。创建知识库和 MD/TXT/CSV 入库不依赖该程序。DOCX、PPTX、XLSX、PDF、HTML、JSON 仍统一用 [Microsoft MarkItDown](https://github.com/microsoft/markitdown) 转换，不静默换解析器。

例如第一次只上传 Word 和 PDF：

```bash
python3 zsk-knowledge-base-skill/install.py --dependencies-only --formats docx,pdf
python3 zsk-knowledge-base-skill/install.py --doctor --formats docx,pdf
```

其他宿主检查已安装组件时仍需传 `--host` 和 `--dest`。依赖补齐命令不复制 Skills，不需要重装已存在组件。

- 合成文件转换已通过：复用现有程序，零下载。
- 格式未通过：先核对当前可执行文件确实属于该 pipx 环境，保留当前版本补对应格式；新装使用验证版本。环境不一致则停止并提示，不降级、不另装无效副本。复用 pip/pipx 缓存，不使用 `--force` 或 `markitdown[all]`。
- 首次安装要提前准备全部 Office/PDF 时，仍可显式用 `--install-markitdown`，也可配 `--formats` 缩小范围。
- 下载失败：保留已完整安装的 ZSK，明确“可建库，富文档未就绪”，稍后仅重试依赖。
- `--doctor` 不带格式只检查建库组件完整性；带格式才以实际转换检查对应能力，不再仅凭 `--version` 判定。
- OCR/页渲染始终是可选增强；完整页证据模式需要另行通过其检查。

Windows 安全软件若拦截 CLI，只核实并处理可信程序的具体路径，不要求关闭全部防护。

## 当前范围与后续范围

- 当前默认：MD、TXT、CSV，以及经 MarkItDown 转换的 DOCX、PPTX、XLSX、PDF、HTML、JSON。
- 当前可选：PDF/PPTX 完整页图、PPT 原生文字、视觉页 300 DPI 多次本地 OCR 严格一致性检查、逐条知识卡原文摘录绑定，以及飞书“脱敏页文字＋高清原页图”的正文/图片数量/尺寸/远端媒体 SHA256 回读。OCR 至少两次高度一致才可继续，部分相似或单次高置信度不会放行；结果写入前再次执行隐私检查。页图需要 `pdftoppm`、`pdfinfo`；本地 OCR Provider 需要 Tesseract 的 `chi_sim` 与 `eng` 语言包。macOS 已安装 Microsoft PowerPoint 时优先使用 PowerPoint 原生导出；首次运行可能出现 macOS 自动化授权提示。没有 PowerPoint 时使用 LibreOffice；PowerPoint 已存在但原生导出失败时停止，不静默降级。
- 不在当前范围：零散图片入库、音视频、图片向量检索、图片描述、猜测式图文对应、无确认自动发布。

## 包含的组件

- `zsk-router`：唯一公开入口，识别建库、入库和状态任务。
- `zsk-ruku`：登记来源、版本、隐私与使用权限。
- `zsk-zhishi`：把已确认资料整理为业务知识。
- `zsk-duibiao`：保留同行完整拆解与可选择、组合的结构方法，明确来源归属。
- `zsk-profile`：整理主体确认事实、运营设定和候选素材。
- `markitdown-skill`：必装的 Microsoft MarkItDown 转换说明与运行边界；供 ZSK 后台和独立文档转换复用，不是第二个入库入口。
- `shared`：以上组件共用的合同、格式读取和飞书／Obsidian 适配代码。

## 建库确认如何跨次执行

首次预览把一次性确认摘要保存在宿主本机持久状态目录（默认 `~/.zsk/confirmations`，可配置 `ZSK_STATE_DIR`）。用户确认后可在另一个 Python 进程继续，使用同一状态目录与任务 ID；30 分钟内有效，消费后不可重放。飞书确认绑定当前用户和租户，账号变化时必须重新预览。只存摘要与期限，不保存凭据或客户正文；本机记录失败则不建库。

## 第一次使用示例

创建个人知识库：

> 请使用 zsk-router，在飞书里为我创建一个私有知识库，名称为“AI学习测试库-我的姓名”。先检查连接、账号和权限，给我看创建预览，等我确认后再创建；创建后请回读并把链接发给我。

上传资料：

> 请使用 zsk-router，把这份 Word 上传到我刚创建的个人知识库。先让我确认你识别到的文件名和使用边界，再入库；完成后请回读并告诉我结果。

## 安全边界

- 不在仓库中保存飞书账号、访问令牌、客户资料或个人隐私。
- 权限、来源、版本、隐私或回读失败时停止。
- 不把课堂模拟价格、库存或交付时间当成真实业务承诺。
