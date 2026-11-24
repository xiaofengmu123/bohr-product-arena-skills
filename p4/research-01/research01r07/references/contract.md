# result.json 字段与证据溯源规范

本文说明 `result.json` 的结构、各字段含义与证据回链的硬性要求。运行脚本按此结构确定性组装，Agent 只需在 `/app/work/` 写实质内容。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取 case.json 的 `case_id/caseId/id/armiId`，缺则空串 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `research_product` | object | 主产物：类型、研究问题、综合结论、结论列表 |
| `evidence_matrix` | array | 逐条证据，每条带 locator + quote |
| `uncertainty` | object | `conflicts` / `biases` / `gaps` / `undetermined` 四类 |
| `sources` | array | 材料来源清单 |
| `change_log` | array | 骨架、merge、finalize 等确定性变更记录 |
| `limitations` / `errors` / `degradations` | array | 边界、错误、降级 |
| `pending_human` | array | 待人工确认的缺口 |
| `runtime_seconds` | number | 从骨架到 finalize 的实际耗时 |

**契约回填**：若 case.json 含 `output_contract`（或等价键），其全部键必须原样出现在 result.json，缺值给 `null`/`[]`，未知键不删除、不改名。

## research_product 与结论强度

- `type`：`synthesis`（综述）/ `screening`（筛选）/ `trend`（趋势）/ `proposal`（选题评估）。
- `conclusions[]`：`id`、`text`、`evidence_ids[]`、`strength`。
- `strength` 三档：`established`（材料内直接证实）、`inferred`（由多证据合理推断）、`conjectural`（推测，需明确标注）。动词与证据强度一致：证实写"证实/表明"，推测写"初步表明/有望"。

## evidence_matrix 与证据回链

每条证据必须有：

- `id`：稳定标识（E1、E2…），重复运行不变。
- `locator`：定位（页码/章节/段落，如 `p3 §2.2`）。
- `quote`：**逐字**引用材料原文；`judgment=extraction_failed` 时可缺 quote，但必须说明提取失败原因。
- `judgment`：`supports` / `partial` / `unsupported` / `conflicts` / `extraction_failed`。
- `conclusion_ids[]`：回指结论，保证"结论 ↔ 证据"双向可查。

**硬要求**：每条结论的 `evidence_ids` 必须命中 `evidence_matrix` 中真实存在的 `id`；反之不强制（证据可尚未被结论引用）。

## uncertainty 的四类

- `conflicts`：两条及以上证据对同一判断结论不一致，逐条列出两个来源 + 解决说明，不静默择一。
- `biases`：来源选择、语言、发表偏倚等偏倚。
- `gaps`：基于检索/材料缺口写"具体缺什么"，不用"尚存争议"等套话。
- `undetermined`：无法裁定、需进一步检索或人工判断的项。

## 六点覆盖（对应评分维度）

`main.py selfcheck` 逐点检查：

1. 问题拆解：`decomposition.md` ≥2 个子问题并标依赖。
2. 检索计划：`search_plan.md` 有顺序、来源范围、停止条件。
3. 证据回链：每行有 locator + quote，结论引用了真实证据 id。
4. 冲突证据：`uncertainty.conflicts` 非空且并列两来源。
5. 综合结论：`research_product.conclusions` 非空且与问题一致。
6. 研究空白：`uncertainty.gaps` 或 `pending_human` 非空且具体。

## 变更记录约定

`change_log` 只记脚本触发的确定性动作（`skeleton` / `merge` / `finalize`），merge 幂等：同一 payload 重复 merge 不会重复追加同一条 `merge` 记录。时间戳是真实运行记录，重复运行允许时间戳不同，但字段布局与证据 id 顺序保持一致。
