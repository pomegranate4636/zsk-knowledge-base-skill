---
name: zsk-duibiao
description: 将已登记的外部参考整理为 04 内容方法资产，只提炼怎么讲，不证明事实、身份、案例、数据或承诺；只由 zsk-router 后台调用。
---

# ZSK Duibiao

## 当前工作

只接收已登记、处理权明确且隐私状态为 `passed` 或 `redacted` 的来源中，被语义识别为可迁移方法的内容单元。来源整体可以是 `unknown` 或 `mixed`。Stage 7 通过共享运行时写入一张或多张 04 方法卡；同一输入重跑复用，不覆盖不同内容。

04 可保存 `peer_content_asset` 或 `content_method_asset`，并用 `applicable_workflows` 声明适用于口播、公众号或两者。旧 `oral_method_asset` 默认只供口播使用。方法资产不输入同行身份、案例、数据、承诺或长段原文。

本阶段不抓取外部内容、不编写正文，也不写 03 或 05。写入由当前绑定的飞书或 Obsidian Adapter 执行，并在 Adapter 内完成内容回读。

## 必须遵守

- 每张方法卡必须回链一个已登记的 `reference_method` 来源。
- 不能把参考来源的身份、公司业务、案例、数据或承诺写进业务知识或 Profile；长段原文不进入方法资产。
- 只沉淀“怎么讲”的机制；不把同行故事、产品事实或效果证明改写后带入。
