# result.json 契约与证据溯源规范

本文件是 `scripts/scaffold.py`（由 `scripts/main.py scaffold` 调用）与
`scripts/finalize.py`（`scripts/main.py finalize`）共同遵守的唯一输出契约。
执行前通读；Agent 只负责把 findings 写成 `/app/work/findings/*.json`，
result.json 的合并、校验、证据溯源由 finalize 完成。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 来自 `/input/case.json`，缺失时为 `""` |
| `status` | enum | `ok` \| `partial` \| `insufficient_evidence` \| `input_error` |
| `output_contract` | object | `case.json.output_contract` **原样回填**（缺值给 `null`/`[]`/`""`，不改名不删未知键）；同时把非保留键提升为顶层字段，保留键见 §5 |
| `summary` | object | `verdict` + `counts` + `by_facet` + `by_finding_type` |
| `result_status` | object | `overall`（通过/问题/待复核/未检查）+ `facets`（五个检查面各自状态） |
| `review_report` | object | `findings`：逐条审查意见（§3） |
| `evidence` | object | `evidence_map`（finding_id → 证据条目列表）+ `numeric_checks`（脚本候选检查） |
| `sources` | array | 实际核验过的来源，见 §4 |
| `uncertainty` | array | 无法确认项 |
| `pending_human` | array | 需人工复核项 |
| `limitations` | array | 未覆盖/未检查项 |
| `errors` | array | 处理中遇到的错误 |
| `degradations` | array | 降级记录（缺工具/缺页/扫描件等） |
| `change_log` | array | 阶段变更记录 |
| `runtime` | object | 墙钟时间与脚本版本（唯一非确定性字段，见 §7） |

## 2. output_contract 回填规则

- `scaffold` 读取 `/input/case.json` 的 `output_contract`；若为 dict，深度复制后写入
  result.json 的 `output_contract` 字段。
- 对值为 `null` 的键保留 `null`；空字符串保留 `""`；空数组保留 `[]`；未知键一律保留。
- 同时把 output_contract 中**不与保留键重名**的键提升到 result.json 顶层，保证
  “result.json 含 output_contract 全部字段”在顶层也成立。
- `finalize` 合并时保留所有非保留顶层字段（§5 之外的键一律透传）。

## 3. finding 对象（`review_report.findings[]`）

必填六元组：`id / facet / locator / quote / severity / suggestion`。

| 字段 | 取值/语义 |
|---|---|
| `id` | `F-001` 起序号的稳定标识 |
| `facet` | `research_design` \| `variable_definition` \| `statistical_methods` \| `sample_size_power` \| `results_reporting` |
| `check_class` | `fact_check` \| `citation_support` \| `method_design` \| `statistical_reporting` \| `argumentation_chain` \| `academic_norms` |
| `finding_type` | `factual_error` \| `insufficient_evidence` \| `normative_issue` \| `advisory` \| `pending_review` |
| `severity` | `critical` \| `major` \| `minor` \| `advisory` \| `pending` |
| `title` | 一句可读标题 |
| `locator` | 可回查定位串：`p.3 ¶2` / `p.4 Table 1` / `p.6 Fig.2` / 纯文本 `¶2` |
| `quote` | **逐字摘录**，不得改写、不得拼接非相邻文字 |
| `suggestion` | 可直接执行的修改建议 |
| `rationale` | 判断依据（方法—数据类型匹配、报告规范、两条证据对比等） |
| `confidence` | `high` \| `medium` \| `low` |
| `review_status` | `confirmed` \| `needs_review` |
| `evidence` | 由 finalize 回填的结构化证据，见 §6 |

约束：`quote` 必须逐字来自原文；`locator` 与 quote 组合必须能回查原文。冲突信息不
隐藏：同一指标两处不一致时，两条证据都记录，finding 的 `evidence.conflicts` 标出
另一处 locator。

## 4. sources 条目

```json
{"id": "SRC-001", "kind": "material|case|task", "path": "materials/paper.pdf",
 "description": "……", "status": "read|partial|needs_ocr|unreadable"}
```

只收录实际核验过的来源；禁止虚构 DOI/URL/数值/来源。

## 5. 保留键（finalize 管理，不被 output_contract 提升覆盖）

`case_id, status, summary, result_status, review_report, evidence, sources,
uncertainty, pending_human, limitations, errors, degradations, change_log,
runtime`。其余顶层键一律透传保留。

## 6. 证据溯源结构（本版强化点）

每条 finding 由 finalize 生成 `evidence` 对象并同步写入
`evidence.evidence_map[finding_id]`（数组，可含多条/冲突条目）：

| 字段 | 语义 |
|---|---|
| `source` | 材料相对路径（如 `materials/paper.pdf`） |
| `segment_id` | `/app/work/segments.json` 中稳定段 id |
| `quote_sha1` | quote 规范化（空白折叠）后的 SHA-1，供复核人校验逐字性 |
| `char_offset` / `char_length` | 在来源文本中的字符区间（首个命中） |
| `context` | 命中处前后各约 60 字符的上下文 |
| `evidence_kind` | `primary`（主证据）\| `secondary` \| `conflict` |
| `verification` | `exact`（逐字命中）\| `none`（未命中，finalize 自动降级为 needs_review） |

`verification=none` 时，finalize 自动把该 finding 的 `review_status` 置为
`needs_review`、`confidence` 置为 `low`，并把该条写入 `uncertainty` 与
`degradations`，绝不冒充已验证证据。

## 7. numeric_checks 候选（脚本自动，需人工确认）

`evidence.numeric_checks[]` 每项含 `id / kind / locator / segments / detail /
status`，`status` 只能是 `candidate`。复核动作：回 locator 读原句，确认 p 与 CI/措辞
指向同一结局、确认 N 是总数还是分组数；确证后提升为 finding，未确证不写或写
`pending_review`。

## 8. 诚实与确定性

- `status` 诚实：空材料 `insufficient_evidence`；缺 case.json 且无材料
  `input_error`；未走完全部检查面 `partial`。
- 除 `runtime`（墙钟时间）与 `change_log` 时间戳外，同一输入重复运行结果一致；无随机数。
