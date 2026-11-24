# 字段规则与证据溯源规范

本文件定义 result.json 与证据映射的字段语义，供 Agent 在语义层补充、供他人复核时对照。

## 1. result.json 顶层字段

| 字段 | 类型 | 语义 |
|---|---|---|
| `case_id` | string | 案例标识，取自 case.json 的 `case_id`/`id`/`challenge_id`，非空 |
| `status` | enum | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `<contract 字段>` | any | 题目 output_contract 全部字段原样回填，缺值 `null`/`[]` |
| `sources` | list | 关键结论证据（item/locator/quote），由 evidence_map 聚合 |
| `uncertainty` | list | `{item, reason, severity}` |
| `change_log` | list | `{time, actor, step, detail}`，UTC ISO 时间 |
| `errors` | list | 硬性失败（lstat 失败、根目录缺失、不可读） |
| `degradations` | list | 降级处理（too_large/non_regular/symlink/坏编码/坏 JSON） |
| `runtime` | object | `{seconds, files, dirs, evidence_count}` |
| `limitations` | list | 已知限制（哈希上限、版本链启发式、语义检索为候选层） |
| `pending_human` | list | 需人工确认项 |
| `computed` | object | 全部机械证据与产物引用 |
| `artifacts` | object | 产物相对路径映射 |

## 2. output_contract 回填规则

1. 自动识别键名：`output_contract`/`output`/`result_schema`/`contract`/
   `output_schema`/`result_contract`/`required_fields`。
2. 遍历 contract 结构，leaf 字段按点路径重建嵌套；缺值给 `null`，list 类型给 `[]`。
3. 按子串匹配映射到 computed 值：manifest/inventory/files→files，directory_tree→
   directory_tree，duplicates/dedup→duplicate_groups，versions→version_candidates，
   aliases→aliases，reconciliation/recovery→reconciliation，index→index，
   answers→answers，summary/report→summary，evidence_map→evidence_map。
4. 无法映射的字段给 `null`，不删键、不改名；与保留键冲突时写 `computed._contract_<字段>`。

## 3. evidence_map 条目（本版重点）

每条结论一条目，字段语义：

| 字段 | 语义 |
|---|---|
| `id` | `ev-0001` 起递增，稳定可引用 |
| `kind` | `file_identity`/`directory`/`dedup`/`version`/`alias`/`registry`/`reconciliation`/`answer` |
| `claim` | 自然语言结论，含具体数值/路径 |
| `method` | 产生结论的方法：`sha256`/`normalized_stem+similarity`/`readlink`/`st_ino`/`registry_parse`/`registry_reconcile`/`inventory_count` 等 |
| `locator` | 相对路径（正斜杠），可追加 `#p页码` 或 `#L行号` |
| `quote` | 逐字片段，或机器可复算值（`sha256=<hex>`、`target=<...>`、`inode=<n>`、`stem=<...> relation=<...>`、`entries=<n>`） |
| `confidence` | `high`（内容哈希/文件系统事实）/`medium`（启发式/机械问答） |
| `basis` | 证据依据：`content_hash`/`filesystem_walk`/`heuristic`/`parse_result`/`path+hash_comparison`/`mechanical_computation` |
| `conflicts` | 对立 evidence id 列表；无冲突为 `[]` |

### quote 规范

- 允许两类：**逐字原文片段**（不改写、不概括）；**可复算值**（哈希、链接目标、
  inode、模式串、归一化主干）。
- 哈希类 quote 必须完整 64 位十六进制，不允许截断。
- 文本类 quote 超出 300 字符用 `…` 截断并在 claim 中保留关键数值。

### 冲突不隐藏

同一指标（如某文件的哈希）在两个来源不一致时：
1. 两个来源各生成一条 evidence_map 条目；
2. 在 `conflicts` 中互相列出对方 id；
3. 顶层 `uncertainty` 增加一条 `severity=high` 的 `source_conflict` 记录。

## 4. 版本链与对账的判定规则

- 归一化主干：小写、去扩展名、去除版本/副本/日期 token
  （`v1/v2/ver/rev/draft/final/copy/副本/修订/终稿/2024-01-02` 等）。
- 相似度：文本文件用 `difflib.SequenceMatcher` 对 token 序列计算 ratio；二进制文件
  仅按大小分组并标 `binary_size`；同哈希标 `duplicate`。
- 对账：注册路径与真实路径归一化后比对；注册哈希仅当为 64 位十六进制时与 SHA-256
  比对，否则标 `hash_note` 跳过验证，不误报 mismatch。

## 5. 诚实性底线

- 材料缺失、哈希无法计算、注册哈希算法不一致、版本链无法确认，都必须显式记录，
  禁止编造文件内容、哈希、元数据、DOI 或运行结果。
- `status` 只依据实际缺口判定，不因"想得高分"而写 `ok`。
