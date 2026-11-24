# result.json 字段规范（result-schema）

本文件是 `/app/output/result.json` 的字段契约与 output_contract 回填规则，供 Agent 落盘时对照，也供他人复核。

## 1. 强制字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 取自输入 `case.json` 的 id 类字段；缺失为 `"unknown"` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error` |
| `results` | array | 纳入候选，每条见下方 record 规范 |
| `screening` | object | 全量筛选账本（included/excluded/uncertain/merged_duplicates + totals） |
| `evidence` | array | 每轮查询/筛选/排序/停止的日志 |
| `sub_questions` | array | 拆解出的可检索子问题 |
| `query_plan` | array | 查询顺序与依赖（前置/并行） |
| `term_expansion` | array | 术语扩展（每概念 ≤6 变体） |
| `stop_decision` | object | 停止判断（status/reason/coverage 等） |
| `sources` | array | 去重后的来源（database/url/record_count） |
| `uncertainty` | object | missing_metadata/conflicts/coverage_gaps/notes |
| `change_log` | array | 每次变更：time/action/reason/status |

## 2. 可选但推荐字段

`research_question`、`case_id`（强制）、`status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`（started_at/seconds_elapsed/model_requests/budget_seconds/budget_requests）。

## 3. record 规范（results 与 screening 中每条）

```json
{
  "rank": 1,
  "title": "…",
  "authors": ["…"],
  "year": 2024,
  "journal": "…",
  "doi": "…",
  "url": "…",
  "database": "paper_search",
  "sub_question_ids": ["SQ1"],
  "relevance": 1.0,
  "relevance_reason": "…",
  "screening": "include",
  "screening_reason": "…",
  "evidence": {"locator": "materials/paper.md#p3", "quote": "逐字摘录"},
  "score": 0.82,
  "score_breakdown": {"relevance": …, "metadata_completeness": …, "source_reliability": …, "weights": {…}}
}
```

`screening` 取值：`include`/`exclude`/`uncertain`/`merged_duplicates`。被排除的项保留在账本 `excluded` 中并带 `screening_reason`，不得静默丢弃。

## 4. output_contract 回填规则（本版重点）

1. 脚本递归扫描 `case.json`，识别 `output_contract`、`output_schema`、`required_fields`、`result_fields`、`output_fields`、`contract`、`schema`、`output` 等键下值为非空 dict 的契约。
2. 从契约中提取字段名：`properties` 键、`required` 列表、`fields`/`items` 键或平面键。
3. result.json 中：
   - `output_contract` 保存发现的全部契约（`{位置: 契约}`）；
   - `output_contract_fields` 保存去重后的字段名清单；
   - 每个契约字段**原样作为顶层键**回填，未获得时为 `null`；不改名、不删未知键。
4. Agent 用 `set-fields --file fields.json` 把实际结果映射进契约命名的字段，例如：

```json
{"candidates": "results", "evidence_log": "evidence", "summary": "plan.md"}
```

   `set-fields` 只做顶层键合并，不会覆盖标准字段的结构（相同键以最后一次 set-fields 为准）。

## 5. 可编辑产物

- `screening.md`：全量筛选清单，含每个 bucket 的全部记录、理由与 locator/quote。
- `plan.md`：研究问题、子问题拆解、查询计划、术语扩展、停止判断。

两者随 `assemble` 每次刷新，供人工复核与修改。