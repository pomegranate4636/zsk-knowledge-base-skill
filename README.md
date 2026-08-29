# ZSK Knowledge Base Skill

一套面向 Codex / WorkBuddy 的通用知识库 Skill 组合。公开入口只有 `zsk-router`，其余组件负责资料登记、业务知识、参考方法和主体资料的安全处理。

## 仓库定位

这是 **ZSK 的唯一更新真源**。所有 ZSK 功能、安装说明和测试先在这里修改、验证并合并到 `main`。

后续只能从这里单向同步到：

```text
zsk-knowledge-base-skill（唯一真源）
→ zhihui-mianmian-skills（客户交付包）
→ content-slim（知识库消费桥接）
→ 客户或讲师本机 ~/.codex/skills（运行安装副本）
```

不要在交付包、Content Slim 或已安装副本中反向修改 ZSK；它们不是更新真源。历史 `content-workflow-skills` 只保留开发与验收记录，不再作为 ZSK 的日常开发入口。

## 一句话理解

```text
客户上传资料
→ zsk-router
→ MarkItDown 转 readable.md
→ 01 来源登记
→ 03 业务知识 / 04 内容方法 / 05 Profile，或 02 待审核
```

客户始终只调用 `zsk-router`。`markitdown-skill` 是必装后台能力，不是第二个入库入口。

资料入库有两次彼此独立的内容确认：先确认文件、页数与 03/04/05 分类，再展示正式知识页草稿并确认发布。确认状态由 `zsk_content_confirmation_v1` 跨轮持久保存；最终批准使用 `zsk_publish_approval_v1`，同时绑定草稿 SHA256 和页面图片清单 SHA256。草稿或图片变化后必须重新确认。

本合并版内置可选的 `haozhai-v1` 策略层。它不改变 ZSK 的通用建库、绑定和 Adapter 架构，只在豪宅 Binding 下强制原 Agent 的 03 六类目录、04 七段拆解、05 三层画像、本人事实独立复核、逐字来源校验以及 PDF/PPTX 页面图片回链。实现位于 `skills/shared/haozhai_policy.py`，Router 不得绕过策略层直接生成豪宅正式草稿。

飞书发布使用可恢复事务：先验证两次内容确认，再把逐页图片分别插入 01 来源文档和引用它们的正式知识页；每张图片插入前查重、插入后回读。`zsk_feishu_publish_receipt_v1` 记录来源链接、正式页链接和阶段状态，网络中断后继续未完成阶段。授权缺失时由 Agent 主动打开飞书原始授权页并展示二维码，不让客户执行命令；挑战码不落盘。

`skills/shared/zsk_entry.py` 是唯一运行时门面。首次新建与绑定已有飞书/Obsidian 库都会在九根结构和规则回读成功后保存唯一当前绑定；建库确认、当前绑定、环境就绪、内容确认和发布收据都能跨任务继续。运行态默认位于安装目录 `.zsk-runtime`，只保存非秘密状态与哈希，不保存飞书 token、设备挑战码或客户正文。

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

## 文档转换前置条件

ZSK 不让大模型直接读取 Office 或 PDF。安装包内含 `markitdown-skill`，它是必装配套能力但不是客户业务入口。MD/TXT/CSV 可直接入库；DOCX、PPTX、XLSX、PDF、HTML、JSON 由 Microsoft MarkItDown 在本机转换为唯一正式 `readable.md`。

首次安装后，用最小格式集合安装并检查转换器；不使用 `markitdown[all]`。PDF/PPTX 的页面图片由本机 Poppler 加 PowerPoint/LibreOffice 渲染，OCR 使用 Tesseract 或 Windows OCR；音视频和联网扩展不属于当前版本：

```bash
python3 zsk-knowledge-base-skill/install.py --install-markitdown
python3 zsk-knowledge-base-skill/install.py --doctor
```

若已安装基础版 MarkItDown：

```bash
pipx inject --force markitdown 'markitdown[docx,pdf,pptx,xlsx]==0.1.6'
python3 zsk-knowledge-base-skill/install.py --doctor
```

Doctor 会同时检查 MarkItDown、PDF 逐页图片、PPT 逐页图片和页面 OCR。任何一项未通过时，PDF/PPTX 会准确停止并进入 02，不会只保存文字后伪称完整，也不会把资料交给大模型。

## 当前范围与后续范围

- 当前：MD、TXT、CSV，经 MarkItDown 转换的 DOCX、XLSX、HTML、JSON，以及带逐页 PNG、OCR 和页码证据链的 PDF/PPTX。
- PDF/PPTX：页面图片是主来源证据，Markdown 与 OCR 只用于检索和草稿辅助；页面缺失、顺序异常或 OCR 失败时进入 02。
- 不在当前范围：零散图片、音视频、图片向量检索和无来源页码的自动图文猜配。

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

> 请使用 zsk-router，把这份 Word 上传到我刚创建的个人知识库。先给出 03/04/05 分类建议并等我确认，再完整展示正式草稿并等我第二次确认；发布后请回读并告诉我两个链接和收据位置。

更完整的客户步骤见 [使用说明-Codex和WorkBuddy.md](使用说明-Codex和WorkBuddy.md)，迁移落点见 [合并版能力与边界.md](合并版能力与边界.md)。

## 安全边界

- 不在仓库中保存飞书账号、访问令牌、客户资料或个人隐私。
- 权限、来源、版本、隐私或回读失败时停止。
- 不把课堂模拟价格、库存或交付时间当成真实业务承诺。
