# 字段级契约（contract）

本文定义 `result.json` 与三个交付产物的字段语义、定位规范与状态语义，是 `check`/`build`/`selfcheck` 的共同依据。

## 1. result.json 顶层

| 字段 | 类型 | 语义 |
|---|---|---|
| `case_id` | string | 从输入 `case.json` 原样带回，禁止改写 |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error` |
| `<output_contract.*>` | 任意 | 逐键回填，缺值 `null`/`[]`，不改名、不删未知键 |
| `result` | object | 盘点摘要 + 去重/版本/别名/冲突分组 + 检索问答 + 索引统计 |
| `claims` | list | 逐条结论，每条带 `claim_id/kind/confidence/text/evidence` |
| `uncertainties` | list | 待人工或启发式不确定项 |
| `sources` | list | 文件来源索引：`source_id/rel_path/sha256/file_type` |
| `uncertainty` | object | `summary/count/items`，供信封层快速查看 |
| `change_log` | list | 操作记录：`step/action/before/after/reason/locator` |
| `pending_human` | list | 待人工裁定项（冲突、缺失文件、恢复） |
| `errors` | list | 输入/读取/解析错误 |
| `degradations` | list | 降级与缺口（`no_draft`、截断、无文本层、输出回退） |
| `runtime` | object | 唯一允许变化的字段（耗时 + 命令 + skill 版本） |

## 2. status 判定（确定性阶梯）

1. 坏 JSON / 缺 `case.json` **且**有材料 → `input_error`（仍盘点材料）。
2. 无文件且无目录 → `insufficient_evidence`。
3. 无 draft（或 draft 为空模板）→ `partial`，`degradations` 标 `no_draft`。
4. 存在 errors / degradations / pending_human / 来源冲突 → `partial`。
5. 其余 → `ok`。

退出码恒为 0：真实问题只写进 `errors`/`degradations`，不靠退出码表达。

## 3. 溯源硬要求

- 每条结论（重复、版本、别名、检索命中、跳过）必须能指回材料逐字片段：
  `source_id` + `locator` + `quote`。
- `source_id` 为 `F001…`（文件）、`case`（case.json 原文）或 `task`（task.md 原文）。
- `quote` 必须能在 `source_id` 对应的 `verifiable_text` 中逐字命中；`check` 阶段机械验证，
  未命中标 `verified=false` 并写入 `check_report.json`，不静默丢弃。
- 冲突信息不隐藏：同一指标两个来源不一致时都记录并标 `source_conflict`。
- 缺值写 `pending_human` 或 `not_reported`，不虚构引用、DOI、数值。

## 4. locator 与确定性

- `locator` 使用 `rel_path`（相对 `/input`，正斜杠）+ `sha256`，不张冠李戴。
- 交付产物除 `runtime` 外不含任何时间戳（mtime/atime/ctime 一律不落盘）。
- 同一输入两次运行，`result.json`（去 `runtime`）与 `library_index.json` 逐字节一致。

## 5. 交付产物

| 文件 | 内容 |
|---|---|
| `result.json` | 结构化结果 + 信封 |
| `library_index.json` | 完整盘点：文件/目录清单 + 分析（别名/版本/冲突/索引） |
| `evidence_map.json` | 逐条 claim/source 的 `locator + 逐字 quote` 映射 |
| `inventory.md` | 可读交接清单（含复跑命令） |
