# 宿主接入与按需依赖

安装、首次建库或依赖检查失败时读取本页。ZSK 源码入口：https://github.com/slbb1995/zsk-knowledge-base-skill 。客户不需要讲师分发 ZIP；按该仓库 README 获取完整组合，不只复制 SKILL.md。

## 先复用，再补缺失能力

1. 确认当前宿主实际加载且持久保存的 Skills 目录。Codex 可用其配置目录；豆包工作、WorkBuddy 和其他宿主必须根据当前环境核实并显式传 `--host`、`--dest`，不要猜用户路径或照抄 Codex 默认位置。豆包现场出现 `.user_skills` 与临时 `.skills` 的差异，以当前宿主证据为准。确认 `shared`、预置资源和六个 Skill 作为完整组合持久存在，不能只确认可发现的 Skill 入口。
2. 只装 ZSK 组件即可建库；安装器默认不下载 MarkItDown 程序。`markitdown-skill` 说明仍随完整组合安装。
3. 只有飞书后端才检查飞书能力；Obsidian 不需要飞书 CLI 或飞书授权。
4. 优先复用宿主内置的 `lark-cli` 和已绑定身份，不重新登录、更新或覆盖宿主管理的程序。当前 Python 适配器需要可执行 CLI；只有连接器名称而无可调用 CLI 时，准确停止，不假装已支持其他传输协议。
5. 运行 `FeishuAdapter.doctor()`。独立 CLI 检查 `auth status`；仅当 CLI 明确表示没有 `auth` / `status` 命令时，改用 `--as user contact +get-user` 核实当前身份。授权过期、拒绝、网络失败或响应损坏不能走这条兼容路径。
6. 宿主管理身份的检查只证明连接可用，不证明 Wiki/Docs 写权限。创建预览必须说明权限尚未完整预验证。确认后仍走 FirstRunBootstrap，实际 API 拒绝即停止；保留并报告已创建对象，最终完整回读才能报告成功。不能为了“通过 doctor”伪造 scopes 或手动跳过建库链路。

## 飞书能力确实缺失时

官方安装入口固定为 https://github.com/larksuite/cli ，按其当前 README 安装发布版和必要 Skills，不要求客户编译源码。不把飞书 CLI 复制进 ZSK，不锁死一个过时安装版本；检查已验证最低版本与真实命令兼容性。

- 豆包等宿主已提供能力但未绑定账号：引导宿主内的飞书连接，不另装独立 CLI 去绕过绑定。
- 独立宿主确实没有 CLI：在用户安装意图范围内按官方说明安装，再走官方用户授权。授权链接/二维码交给用户本人完成。
- 已有独立 CLI：先复用，只有实际能力不足时才按官方更新说明处理。版本格式接受 `1.0.94`、`v1.0.94+5092114`，不把格式差异当成程序缺失。

## 首次富文档入库再补转换依赖

MD/TXT/CSV 不需要 MarkItDown。DOCX/PDF/PPTX/XLSX/HTML/JSON 入库前，根据本次实际文件格式执行检查，例如：

```bash
python3 install.py --host doubao --dest /已核实的持久Skills目录 --doctor --formats docx,pdf
```

对应格式缺失才补齐：

```bash
python3 install.py --dependencies-only --formats docx,pdf
```

该命令用合成文件实测转换，已通过则零下载；仅为缺失格式补依赖：新装使用验证版本，已有 pipx 转换器保留实测的当前版本，复用 pipx/pip 缓存，不用 `--force` 或 `markitdown[all]`。不自动换未经确认的镜像。官方来源：https://github.com/microsoft/markitdown 。下载失败保留可建库的 ZSK，明确富文档未就绪，重试依赖即可，不重装整套 Skill。

`--doctor` 无 `--formats` 只判断建库组件完整性，不宣称所有格式可用。`--version` 不支持时允许验证同一 CLI 的帮助信息，并在证据中记录版本未报告；以实际转换结果决定是否可用，不编造版本。正式来源的隐私检查、非空正文与写后回读保持不变。

## 跨次执行的建库确认

`FirstRunBootstrap` 把确认存入宿主本机持久记录，默认 `~/.zsk/confirmations`，可用绝对路径 `ZSK_STATE_DIR` 指定宿主自己的持久状态根，或在构造器传 `confirmation_dir`。预览与用户确认后的执行必须使用同一状态位置和 `task_id`；不能用每次变化的临时任务目录。无需保持同一个 Python 进程。

预览只写本机确认摘要，不创建飞书/Obsidian 对象。摘要绑定任务、后端、目标、模板、预置包和飞书用户＋租户；不保存凭据、用户资料或正文。有效期 30 分钟，一次性消费；账号切换、目标变化、过期、重复使用或状态不可读时停止，重新展示预览。提交创建前再次检查当前账号。中途失败的旧确认不能自动重试，已有对象仍按原规则报告，不删除。

依赖修复前核对当前 `markitdown` 可执行文件与 pipx 的应用路径是同一个文件，再从该环境查询真实版本。不同 Python 环境、路径不明、指定转换器或版本无法核验时，停止自动安装并指出原因；不能为了补一个格式创建第二套不生效的环境，也不能降级已有转换器。检查参数与安装参数互斥。
