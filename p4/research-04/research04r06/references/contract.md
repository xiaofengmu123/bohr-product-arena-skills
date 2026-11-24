# 契约字典：result.json / plan.json / figure_model.json

本文件是三份结构化产物的字段字典与枚举约定，供人工复核、二次开发与跨工具复用。所有产物均为 UTF-8、合法 JSON。

## 1. result.json

顶层必须含 `case_id`、`status` 与题目 `output_contract` 的全部字段（存在则原样回填，缺值给 `null`/`[]`），再合并下列固定字段。

| 字段 | 类型 | 说明 |
|-|-|-|
| case_id | string | 取 case.json 的 case_id/id/challenge_id，缺则 task.md 的 challenge_id，再无 `"unknown"` |
| status | enum | `ok \| partial \| insufficient_evidence \| input_error` |
| research_question | object | `{question, scope, sub_questions[], domain_note}` |
| protocol | object | `{inclusion_criteria[], exclusion_criteria[], search_scope, date_definition_note}` |
| time_trend | object | `{unit_note, distribution[], windows[], claims[]}` |
| topic_evolution | object | `{stage_divisions[], term_mapping[], events[], claims[]}` |
| representative_works | object | `{criteria[], stages[]}` |
| research_gaps | object | `{gaps[], excluded_not_gaps[]}` |
| evidence_map | list | 每条 `{id, claim, locator, quote, source_file, status, confidence}` |
| conflicts | list | `{items[], resolution}` |
| artifacts | object | 主产物与中间表示的文件相对路径 |
| sources | list | 文献来源清单 |
| uncertainty / pending_human / change_log / limitations / errors / degradations | list | 结论边界与运行痕迹 |
| runtime | object | `{started_at, updated_at, seconds_elapsed, checkpoint}` |

### 枚举与子结构

- `time_trend.distribution[]`：`{year, count, doc_ids[]}`。
- `time_trend.claims[]`：`{claim, support, evidence_ids[]}`；`support ∈ reported|not_reported|inferred`。
- `topic_evolution.events[].type`：`emergence | merge | split | decline | terminology_drift`。
- `topic_evolution.term_mapping[]`：`{canonical, variants[], first_year, last_year}`。
- `representative_works.stages[].works[].kind`：`origin | method | milestone | benchmark`。
- `research_gaps.gaps[].type`：`open_question | method_failure | unresolved_conflict | coverage_absence`；每条必填 `exclusion_evidence`。
- `evidence_map[].status`：`reported | not_reported | source_conflict | inferred`。
- `change_log[]`：`{time, action, reason, before, after}`。

### 诚实性约定

- `quote` 必须为原文逐字引用；无法定位则改精确引用或置 `status=not_reported` 并写入 uncertainty。
- 冲突证据两方都记录并标 `source_conflict`，在 `conflicts` 给出分辨率或“未决”。
- `status=ok` 仅在四类产物齐全且关键 claim 均有证据回链时给出；否则 `partial`/`insufficient_evidence`。

## 2. plan.json（冻结分析计划）

阶段 1 写一次后只读不改；改计划必须记 change_log。

| 字段 | 说明 |
|-|-|
| schema | `"plan-v1"` |
| case_id | 与 result.json 一致 |
| frozen | 恒为 `true` |
| research_question | 研究问题拆解（子问题须可核对） |
| protocol | 纳排规则、检索范围、年份口径（在线发表/见刊/预印本） |
| term_grouping_rules | `{normalize, synonym_map, stopwords, note}` 术语归并规则 |
| window_rules | `{min_window, max_window, split_gap, note}` 时间窗划分规则 |
| budget | `{total_seconds, model_requests, tokens_per_response, checkpoints[]}` |
| change_log | 冻结与后续修改记录 |

## 3. figure_model.json（证据图元模型）

阶段 2 机械生成；把原料归一为可反查的文档/术语/候选句模型，是原料与结论之间的唯一桥接。

| 字段 | 说明 |
|-|-|
| schema | `"figure-model-v1"` |
| documents[] | `{doc_id, source_file, locator, title, year, year_source, citation_count, char_count, is_json_record, machine}`；`year_source ∈ metadata|title_header|body_frequent|missing` |
| term_index[] | `{canonical, variants[], count, first_year, last_year, doc_ids[], machine}` |
| candidates[] | `{kind, doc_id, locator, quote, matched_pattern, machine}`；`kind ∈ future_work|limitation|unknown|conflict|gap` |
| windows[] | `{period, doc_count, doc_ids[]}` |

### 回链路径

`result.json.claims[].evidence_ids[]` → `result.json.evidence_map[].id` → 该条 `locator + source_file + quote` → `figure_model.documents[].locator` → `/app/work/materials/mat_NNN.txt` 的行号或记录号。任何结论均可沿此链回到原文逐字核对。

## 4. 机械值与人工核验

- `machine=true` 表示脚本确定性抽取的**候选**，未经人工/模型核验；核验后更新 `support`/`status`/`confidence` 并删除 `machine` 标记，未核验的保留标记并写入 `pending_human`。
- 重复运行：doc_id 按相对路径排序稳定分配，所有机械值确定性排序，产出逐字节一致。
