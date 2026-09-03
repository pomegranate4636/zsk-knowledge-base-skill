# content-source-v1 + OCR 合并与发布包 TDD 证据

## 范围

- 合并 `origin/main` 的 8 个提交与本地分支提交。
- 同时保留 `content-source-v1` 资产合同和 OCR 页级证据合同。
- 从干净 Git 提交构建唯一完整 ZIP，排除测试、开发工具、`__pycache__`、`.pyc` 和 `.pyo`。

## 用户旅程与验收条件

1. 资料进入 03 知识库时，资产 frontmatter 保留 `content-source-v1` 的 ID、类型、确认状态和适用工作流。
2. 引用 PPT/PDF 页级事实时，正文和元数据同时保留页码、原文与证据哈希。
3. 安装包同时包含 7 个组件、全部 shared 运行模块和 3 份合同 Schema。
4. ZIP 只包含安装所需的 Git 跟踪文件及构建清单；清单记录来源提交和逐文件 SHA-256。
5. 解压后的安装包可独立通过 `install.py --package-check`，并可在临时目录完成安装和安装后检查。

## RED

### 合同冲突

命令：

```powershell
py -X utf8 -m unittest tests.test_knowledge_page_evidence -v
```

结果：合并后的 `stage6_knowledge.py` 只保留 `content-source-v1` frontmatter，合法页级事实测试因正文缺少“第 1 页”失败。

RED 检查点：`fadd8de test: merge content source and OCR contracts with RED evidence`

### 发布包合同

命令：

```powershell
py -X utf8 -m unittest tests.test_install_doctor tests.test_release_package -v
```

结果：测试加载 `tools/build_release_package.py` 时得到 `FileNotFoundError`，证明构建能力尚不存在。

RED 检查点：`a9f2840 test: define clean ZSK release package contract`

## GREEN

### 合同合并

命令：

```powershell
py -X utf8 -m unittest tests.test_knowledge_page_evidence tests.test_install_doctor tests.test_content_source_contract tests.test_page_evidence -v
```

结果：50 项通过。

实现提交：`8b9bcca fix: preserve content source and OCR evidence contracts`

### 发布包构建器

命令：

```powershell
py -X utf8 -m unittest tests.test_install_doctor tests.test_release_package -v
py -X utf8 install.py --package-check
```

结果：19 项通过；仓库安装包结构检查为“完整”。

### 全量回归

命令：

```powershell
py -X utf8 -m unittest discover -s tests -p 'test_*.py' -v
```

结果：101 项测试执行完成，100 项通过；1 项因当前 Windows 账户没有创建 symlink 的权限而明确跳过，生产代码仍保持 symlink 拒绝逻辑。

## 已知验证边界

- 当前 Python 环境未安装 `coverage`，因此没有生成覆盖率百分比；本次以 101 项全量测试和发布包解压安装验收作为回归证据。
- 未改写用户真实 Codex/WorkBuddy Skills 目录，也未执行真实飞书写入；发布包只在隔离临时目录做安装验收。
