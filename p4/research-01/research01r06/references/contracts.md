# 输出契约与中间表示规范

本文件是 `result.json` 与 `/app/work` 结构化中间产物的字段规范，供 Agent 在填表时对照，也供复核者按同一标准核验。

## 1. result.json 组装规则

- `result.json` 是最终事实依据。模型只写 `/app/work` 的中间产物与 `payload.json`，由 `main.py merge/finalize` 确定性组装，避免手工改 JSON 出错。
- 字段来源优先级：题目 `output_contract` 全部字段（缺值 `null`/`[]`，原名原样）→ 稳定信封 `case_id/status/sources/uncertainty/change_log/limitations/errors/degradations/pending_human/runtime_seconds` → 主产物 `research_product/evidence_matrix`。
- 未知键不得删除、不得改名；未知且无法取值的键给 `null` 或 `[]`。

## 2. status 语义

| 值 | 含义 | 触发示例 |
|---|---|---|
| `ok` | 结论完整且每条结论有证据支持 | 全部子问题有证据、综合结论可回链 |
| `partial` | 已完成部分子问题，其余有明确缺口 | 时间到或仅部分材料可读 |
| `insufficient_evidence` | 材料不足，无法形成结论 | 空输入、材料缺失 |
| `input_error` | 输入无法解析 | 坏 JSON、缺 case.json |

规则：宁可 `partial`/`insufficient_evidence`，不可把推测写成 `ok`。

## 3. evidence_matrix 行字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 稳定唯一（E1..En），重复运行不变 |
| `subquestion` | 是 | 命中的 SQ id |
| `source` | 是 | 材料相对路径（如 `materials/a.pdf`） |
| `locator` | 是 | 精确定位（页/节/段落，如 `p3 §2.2`） |
| `quote` | 是 | 逐字引用，必须来自材料 |
| `claim` | 是 | 该片段支持的论断（一句话） |
| `judgment` | 是 | `supports/partial/unsupported/conflicts/extraction_failed` |
| `reason` | 建议 | 为什么作此判定 |
| `conclusion_ids` | 建议 | 反向引用结论 |
| `figure_ids` | 可选 | 关联 figure_model 条目 |

## 4. uncertainty 四类

- `conflicts`：每条必须并列 ≥2 个来源（`sources` 数组，或 `source_a`/`source_b`）+ `resolution` 说明如何处理；不得静默取一方。
- `biases`：来源、检索式、时间窗或纳入规则可能带来的偏倚。
- `gaps`：基于检索缺口/材料缺失的**具体**空白，禁止“尚需进一步研究”一类套话。
- `undetermined`：无法裁定且并列保留的项。

## 5. 结构化中间产物

### plan.json（问题拆解 + 检索计划，单一事实源）

```json
{"schema_version":"1.0","research_question":"…","task_type":"synthesis|screening|trend|proposal",
 "subquestions":[{"id":"SQ1","text":"…","facets":{"object":[],"mechanism":[],"method":[],
   "time_scope":"","comparator":"","evidence_requirement":""},"depends_on":[],
   "search":{"query":"…","sources":["…"],"stop_condition":"…"}}],
 "search_order":["SQ1","SQ2"],"parallel_groups":[["SQ2","SQ3"]],
 "stop_conditions":{"global":"…","per_sq":"…"}}
```

`depends_on` 声明前置关系；`search_order` 决定执行顺序；`parallel_groups` 标注可并行；`stop_conditions` 声明何时停止追加检索。`decomposition.md` 与 `search_plan.md` 由 `main.py plan` 从本文件派生，不得另写一遍造成两处不一致。

### figure_model.json（图/表/附录清单）

```json
{"schema_version":"1.0","figures":[
  {"id":"F1","source":"…","locator":"p5","kind":"figure|table|appendix|diagram",
   "caption":"…","claims":[{"text":"…","evidence_id":"E1"}]}]}
```

用于：把图/表内的关键论断回链到证据行；记录无文本层 PDF 的 `extraction_failed` 情形；供人工复核定位。

### evidence_cards.jsonl（证据卡，一行一条）

```json
{"id":"E1","subquestion":"SQ1","source":"…","locator":"…","quote":"…","claim":"…",
 "judgment":"supports","reason":"…","conclusion_ids":["C1"],"figure_ids":[]}
```

`quote` 必须逐字；`locator` 必须精确；由 `main.py evidence --card` 校验后追加。

### tool_calls.jsonl（工具调用留痕）

```json
{"step":1,"tool":"search|fetch|pdf_text|pdf_render|ocr|table_extract|compute",
 "input":{…},"output_ref":"…","ok":true,"error":"","ts":"…"}
```

每次工具调用记一行，含输入参数、输出引用、失败与重试；复核者可据此重放整条路径。
