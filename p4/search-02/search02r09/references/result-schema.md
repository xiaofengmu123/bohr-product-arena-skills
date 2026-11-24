# result.json 字段规范

`assemble` 生成的 `/app/output/result.json` 是主产物。所有字段都为 UTF-8，未知键保留，不删除。

## 顶层字段（`*` 为强制）

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id`* | string | 取自输入的 id/case_id/challenge_id；无法识别时为 `"unknown"` |
| `status`* | string | `ok` / `partial` / `insufficient_evidence` / `input_error`，必须诚实 |
| `research_question` | string\|null | 原研究问题 |
| `sub_questions`* | array | 拆解出的子问题，每条建议含 `id`/`question`/`object`/`mechanism`/`method`/`time_range`/`comparison`/`evidence_requirement` |
| `query_plan`* | array | 查询顺序与依赖，每条建议含 `id`/`query`/`depends_on`/`parallel`/`rationale` |
| `term_expansion`* | array | 术语扩展，每条建议含 `concept`/`variants`/`language`/`note` |
| `stop_decision`* | object | `status`(`done`/`handoff`/`continuing`)/`reason`/`threshold`/`marginal_gain` |
| `results`* | array | 全部候选记录，按 score 降序，含 rank 与筛选状态 |
| `screening`* | object | 全量筛选账本：`included`/`excluded`/`uncertain`/`merged_duplicates` |
| `evidence`* | array | 查询/轮次/冲突/缺口日志 |
| `sources`* | array | 去重后的 `{source, url}` |
| `uncertainty`* | object | `missing_metadata`/`conflicts`/`coverage_gaps`/`notes` |
| `change_log`* | array | `time`/`action`/`reason`/`status` |
| `status_notes` | string | 状态补充说明 |
| `limitations`* | array | 材料/方法局限，缺数据时如实标注 |
| `errors`* | array | 可恢复错误 |
| `degradations`* | array | 降级处理（超 2MB、坏编码、缺目录等） |
| `pending_human`* | array | 需人工补充的具体缺口 |
| `runtime`* | object | `seconds`/`requests`/`started` |

## results 条目

每条候选记录至少含：

- `rank`：全量排序位次（1 起）
- `title`/`authors`/`year`/`journal`/`doi`/`url`/`database`：元数据，缺失给 `null`
- `sub_question_ids`：命中的子问题 id 数组
- `relevance`：0–1 相关性
- `relevance_reason`：相关性依据（非空泛评语，须指向 locator）
- `screening`：`included`/`excluded`/`uncertain`/`duplicate`
- `screening_reason`：筛选理由
- `evidence`：`{locator, quote}`，quote 必须逐字引用来源原文
- `score`：综合分；`score_breakdown`：`{relevance, metadata_completeness, source_reliability, formula}`

透明打分公式（脚本固化，缺省补 0）：

```text
score = 0.6 * relevance + 0.2 * metadata_completeness + 0.2 * source_reliability
```

## 可编辑产物

- `screening.md`：全量筛选清单（included/excluded/uncertain/merged）
- `plan.md`：拆解、查询规划、术语扩展、停止判断

## 约定

- 不编造文献、标题、作者、年份、期刊、DOI、数值、来源或评测分数；无材料写“材料不足”。
- 冲突信息不隐藏：两个来源不一致时全部保留并标 `source_conflict`。
- `output_contract` 的全部字段原样回填：未填给 `null`/`[]`，不改名、不删未知键。
