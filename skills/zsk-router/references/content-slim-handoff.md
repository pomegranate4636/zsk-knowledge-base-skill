# Content Slim 交接规则

只在用户明确要把当前 ZSK Obsidian 知识库连接给 Content Slim 时读取。飞书知识库当前不能直接交接，因为 Content Slim 只读取本地文件。

## 目标

ZSK 负责生成 Content Slim 公开合同要求的 Manifest，并在当前宿主持久 Registry 中登记当前客户。Content Slim 不需要知道配置来自 ZSK。

默认目录映射固定为：

- knowledge：`03-业务知识库`
- method：`04-内容方法库`
- profile：`05-IP-Profile`
- output：`07-生产与反馈`
- Manifest：`06-Agent与Workflow/content-client-manifest.json`

## 执行

1. 从当前已回读的 ZSK Binding 取得 `client_id` 和 Obsidian 绝对路径，不重新生成或猜测。
2. 定位已安装的 `shared/configure_content_slim.py`。路径缺失或脚本不可读时停止。
3. 只有确认当前宿主就是 Codex 时，第一次调用才可只传 `--vault-root`、`--client-id` 和用户选择的 `--speaker-mode`。WorkBuddy 或其他宿主必须先定位真实持久位置并显式传 `--registry` 与 `--runs-root`；无法定位时停止。
4. 完整展示脚本返回的知识库、Registry、Runs、Manifest、讲述者模式和写入动作，然后结束当前轮次。预检必须零写入。
5. 用户明确确认后，在下一轮以完全相同参数再次调用，并把预检返回的 `confirmation` 原样传入 `--confirmation`。
6. 只有 Manifest、Registry、Runs 全部写入或复用并回读一致，才能说连接完成。Content Slim 尚未真实安装时，只能说配置已准备，不能说内容工作流可用。

示意命令：

```bash
python3 /真实/Skills/shared/configure_content_slim.py \
  --vault-root /客户/Obsidian知识库 \
  --client-id CLT-XXXXXXXXXXXXXX \
  --speaker-mode neutral
```

确认轮次追加：

```bash
--confirmation 预检返回的完整值
```

## 停止条件

- 当前 Binding 不是 active Obsidian；
- 知识库、03/04/05/06/07、Registry 或 Runs 路径缺失、不安全、含软链接或无法回读；
- 相同 `client_id` 已指向另一知识库，或同一知识库已用另一 `client_id` 登记；
- 已有 Manifest 与当前客户、目录或讲述者模式不一致；
- `personal_ip` 模式下不是恰好一份 `active + primary` Profile；
- 用户确认与当前预览不一致。

Registry 已有其他客户时，可以在确认后原子合并当前客户并保留原记录。Content Slim 第一次运行只会在 Registry 恰好有一个客户时自动采用；多个客户必须由用户明确选择。
