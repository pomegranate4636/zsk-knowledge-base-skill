# ZSK Knowledge Base Skill

一套面向 Codex / WorkBuddy 的通用知识库 Skill 组合。公开入口只有 `zsk-router`，其余组件负责资料登记、业务知识、参考方法和主体资料的安全处理。

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

## 与 Content 工作流配合

两套仓库保持独立，推荐顺序是：

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

## 课堂上只说这一句话

把下面这句话完整发给 Codex 或 WorkBuddy：

> 请从 https://github.com/slbb1995/zsk-knowledge-base-skill 安装完整的 ZSK 知识库 Skill 组合及 MarkItDown 配套 Skill。先阅读仓库 README，不覆盖任何已有同名目录；安装到你当前正在使用的 Skills 目录，并补齐 MarkItDown 的 DOCX、PPTX、XLSX、PDF 转换依赖。安装后检查 zsk-router、zsk-ruku、zsk-zhishi、zsk-duibiao、zsk-profile、markitdown-skill 和 shared 是否齐全，并告诉我是否需要重新打开当前任务。

安装完成后，重新打开一个任务，再发送：

> 请检查 zsk-router 是否已经可以使用，并告诉我当前是否具备连接飞书和创建个人知识库的条件。先检查，不要创建。

## 开始前的前置条件

- 电脑上已经可以使用 Codex 或 WorkBuddy。
- WorkBuddy 已经连接本人飞书账号。
- 当前账号具备创建飞书知识库的权限。
- 如果使用飞书后端，本机需要有可用的 `lark-cli`，并完成用户身份授权。

这些条件没有通过时，ZSK 会准确停止，不会假装已经创建或写入。

## 手动安装

默认安装到 `~/.codex/skills`：

```bash
git clone https://github.com/slbb1995/zsk-knowledge-base-skill.git
python3 zsk-knowledge-base-skill/install.py --install-markitdown
```

安装到其他 Skills 目录：

```bash
python3 zsk-knowledge-base-skill/install.py --dest /你的/Skills/目录 --install-markitdown
```

只检查、不写入：

```bash
python3 zsk-knowledge-base-skill/install.py --check
```

安装器不会覆盖已有同名目录。发现冲突时会停止，并列出需要人工处理的目录。

如果 MarkItDown 下载失败，安装器会显示安装工具返回的最后错误，并回滚本次新增的 ZSK 组件，不会留下“Skill 看似已安装、Office/PDF 实际不能入库”的半安装状态。检查网络后重新执行同一条安装命令即可。

Windows 出现安全软件拦截 `lark-cli.CMD` 或其可信 Node.js 运行程序时，不要直接关闭或卸载全部安全软件。先核对文件来自已安装的飞书 CLI，再只对该可信路径放行；无法确认来源时停止飞书操作并请现场管理员处理。

## 文档转换前置条件

ZSK 不让大模型直接读取 Office 或 PDF。安装包内含 `markitdown-skill`，它是必装配套能力但不是客户业务入口。MD/TXT/CSV 可直接入库；DOCX、PPTX、XLSX、PDF、HTML、JSON 由 Microsoft MarkItDown 在本机转换为唯一正式 `readable.md`。

首次安装后，用最小格式集合安装并检查转换器；不使用 `markitdown[all]`，因为页图 OCR 由 ZSK 的独立本地 Provider 负责，音视频和联网扩展不属于当前版本：

```bash
python3 zsk-knowledge-base-skill/install.py --install-markitdown
python3 zsk-knowledge-base-skill/install.py --doctor
```

若已安装基础版 MarkItDown：

```bash
pipx inject --force markitdown 'markitdown[docx,pdf,pptx,xlsx]==0.1.6'
python3 zsk-knowledge-base-skill/install.py --doctor
```

Doctor 未通过时，富文档会准确停止并进入 02；不会静默换用另一套解析器，也不会把资料交给大模型。Doctor 还会分别显示 PDF/PPTX 页级渲染与本地 OCR 状态；这些增强能力不可用不会阻断普通文字入库，但启用完整页证据时会 fail closed。

## 当前范围与后续范围

- 当前默认：MD、TXT、CSV，以及经 MarkItDown 转换的 DOCX、PPTX、XLSX、PDF、HTML、JSON。
- 当前可选：PDF/PPTX 完整页图、PPT 原生文字、视觉页 300 DPI 多次本地 OCR 严格一致性检查、逐条知识卡原文摘录绑定，以及飞书“脱敏页文字＋高清原页图”的正文/图片数量/尺寸/远端媒体 SHA256 回读。OCR 至少两次高度一致才可继续，部分相似或单次高置信度不会放行；结果写入前再次执行隐私检查。页图需要 `pdftoppm`、`pdfinfo`；本地 OCR Provider 需要 Tesseract 的 `chi_sim` 与 `eng` 语言包。macOS 已安装 Microsoft PowerPoint 时优先使用 PowerPoint 原生导出；首次运行可能出现 macOS 自动化授权提示。没有 PowerPoint 时使用 LibreOffice；PowerPoint 已存在但原生导出失败时停止，不静默降级。
- 不在当前范围：零散图片入库、音视频、图片向量检索、图片描述、猜测式图文对应、无确认自动发布。

## 包含的组件

- `zsk-router`：唯一公开入口，识别建库、入库和状态任务。
- `zsk-ruku`：登记来源、版本、隐私与使用权限。
- `zsk-zhishi`：把已确认资料整理为业务知识。
- `zsk-duibiao`：只提炼外部参考的表达方法。
- `zsk-profile`：整理主体确认事实、运营设定和候选素材。
- `markitdown-skill`：必装的 Microsoft MarkItDown 转换说明与运行边界；供 ZSK 后台和独立文档转换复用，不是第二个入库入口。
- `shared`：以上组件共用的合同、格式读取和飞书／Obsidian 适配代码。

## 第一次使用示例

创建个人知识库：

> 请使用 zsk-router，在飞书里为我创建一个私有知识库，名称为“AI学习测试库-我的姓名”。先检查连接、账号和权限，给我看创建预览，等我确认后再创建；创建后请回读并把链接发给我。

上传资料：

> 请使用 zsk-router，把这份 Word 上传到我刚创建的个人知识库。先让我确认你识别到的文件名和使用边界，再入库；完成后请回读并告诉我结果。

## 安全边界

- 不在仓库中保存飞书账号、访问令牌、客户资料或个人隐私。
- 权限、来源、版本、隐私或回读失败时停止。
- 不把课堂模拟价格、库存或交付时间当成真实业务承诺。
