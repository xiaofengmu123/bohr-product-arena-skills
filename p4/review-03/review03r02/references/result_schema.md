# result.json 完整契约

`/app/output/result.json` 是机器可复核的主产物；`/app/output/report.md` 是人类可读、
可编辑的副本。两者必须同时存在。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 保留输入标识，取 `case.json.case_id`（缺失则 `id`/`armiId`） |
| `status` | enum | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，必须诚实 |
| `summary` | object | `verdict` + `counts` + `by_facet` + `by_type` |
| `review_report` | object | `findings` 数组（逐条问题） |
| `result_status` | enum | `pass` \| `problem` \| `needs_review` \| `unchecked`（通过/问题/待复核/未检查） |
| `evidence` | object | `evidence_map`（每条 finding 的 locator+quote+状态）+ `segments_file` |
| `sources` | array | 只含实际核验来源，无虚构 DOI/URL |
| `uncertainty` | array | 无法确认事项 |
| `pending_human` | array | 需人工处置事项 |
| `limitations` | array | 缺口与未检查项 |
| `errors` / `degradations` | array | 输入/软件/解析异常 |
| `change_log` | array | 确定性变更记录（seq/action/phase/note） |
| `runtime` | object | `started_at` / `finished_at`（墙钟，允许随运行变化） |
| `numeric_checks` | array | 脚本自动候选检查，须人工确认后提升为 finding |
| `progress` | object | 五个 facet 的 `pending/done/unchecked` 状态 |
| `checked_classes` | object | 六个 check_class 的命中计数 |

`case.json` 若含 `output_contract`，其**全部字段原样回填**到 result.json 顶层：
缺值给 `null`，不改名、不删未知键；`finalize` 合并时保留所有未知顶层字段。

## finding 对象（review_report.findings[]）

必填六键（缺一即为 schema 错误）：

| 键 | 说明 |
|---|---|
| `id` | 唯一标识，如 `F001` |
| `facet` | 五个 facet 之一 |
| `locator` | 可回查定位串，如 `p.3 ¶2` / `p.4 Table 1` |
| `quote` | 逐字摘录原文 |
| `severity` | `critical/major/minor/advisory/pending` |
| `suggestion` | 给作者的可执行修改建议 |

建议补齐：`finding_type`（五类）、`check_class`（六类）、`basis`（判断依据）、
`confidence`（`high/medium/low`）、`review_status`（`confirmed/needs_review`）。

## 状态推导（finalize 自动执行，可被 meta.json 覆盖）

- 有 critical → `result_status=problem`；
- 有 finding 但无 critical → `problem`；
- 无 finding 且 `status=partial` → `unchecked`；
- 空材料 → `insufficient_evidence`；缺 case.json 且无材料 → `input_error`。
