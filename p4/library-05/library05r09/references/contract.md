# 字段级契约与定位规范

本文是 `SKILL.md` 的补充说明，定义 `result.json` 各字段语义、证据定位规范与状态判定。

## 1. result.json 顶层字段

| 字段 | 语义 | 缺失时 |
|---|---|---|
| `case_id` | 从 `case.json` 原样带回，绝不改写 | 用 `unknown` 并记 `errors` |
| `status` | `ok \| partial \| insufficient_evidence \| input_error` | 必填 |
| `<output_contract 各键>` | 逐键回填，不改名、不删未知键 | `null` 或 `[]` |
| `result` | 盘点摘要、分组、查询结果 | 空对象 |
| `claims` | 逐条结论，每条带 `claim_id/kind/confidence/text/evidence` | `[]` |
| `sources` | 全部来源（文件 + case/task 清单），带 sha256 | `[]` |
| `uncertainty` | 对象：`summary/count/items` | 空对象 |
| `uncertainties` | 与 `uncertainty.items` 同源 | `[]` |
| `pending_human` | 待人工裁定的冲突/版本/缺失 | `[]` |
| `change_log` | 操作记录，含 `op/before/after/reason/locator` | `[]` |
| `errors` | 读取/解析失败，逐条记录 | `[]` |
| `degradations` | 降级说明（缺工具、坏编码、超限等） | `[]` |
| `runtime` | 阶段时间戳与耗时；确定性对比时排除 | 空对象 |

## 2. 证据定位（locator）规范

- 文件证据：`source_id` 为文件编号 `F001…`，`locator` 为相对路径 `materials/paper.md`；
  文本内命中时追加行号：`materials/paper.md#L12`。
- 输入清单证据：`source_id` 为 `@case`（case.json）或 `@task`（task.md）。
- `quote` 必须是材料中**逐字**片段；机械校验阶段会核对 `quote in 源文本`，命中失败记为 `bad_evidence`。
- 哈希：`sha256` 小写十六进制，同一内容 => 同一哈希，用于重复文件与别名判定。

## 3. 分组判定（确定性，可被 draft 覆盖）

| 类型 | 判定 | 置信度 |
|---|---|---|
| `alias_groups` | sha256 完全一致 | `high` / `directly_read` |
| `version_groups` | stem 归一化一致、哈希不同、至少一名带版本标记 | `heuristic`，draft 指定 canonical 后升级 `explicit` |
| `conflicts` | stem 归一化一致、哈希不同、无版本标记 | `heuristic`，进 `pending_human` |

## 4. status 判定（诚实优先）

- 盘点与判断齐备且证据命中 → `ok`；
- 有确定性结果但缺模型 draft / 缺部分语义 → `partial`；
- 无材料 / 无文本层且无可用结论 → `insufficient_evidence`；
- `case.json` 缺失、坏 JSON、输入目录不存在 → `input_error`。

## 5. 确定性要求

同一输入两次运行，除 `runtime` 外逐字节一致：文件按 `rel_path` 字典序编号、
所有分组成员排序、JSON 键序固定、不使用随机数与当前时间戳写入 payload。
