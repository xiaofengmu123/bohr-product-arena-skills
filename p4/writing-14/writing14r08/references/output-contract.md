# 输出契约与字段词表（references/output-contract.md）

本文件定义 `result.json` 的字段语义、七要素结构、来源结构与规范化词表；脚本 `scripts/main.py check` 按本文件校验。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 与 case.json 的 `case_id`/`id` 一致，必填 |
| `status` | enum | `complete` / `partial` / `failed`，如实填写 |
| `title` | string | 开题方案标题（通常为 output_contract 字段） |
| `summary` | string | 一段话摘要；含 `limitations` 时在段末说明 |
| `proposal` | object | 七要素主体（见下） |
| `evidence_map` | array | 结论 → 证据的扁平映射（见 source 结构） |
| `artifacts` | object | 可编辑产物相对路径，如 `{"proposal":"proposal.md",...}` |
| `sources` | array | 材料来源清单，元素为 source 对象 |
| `uncertainty` | array | 缺口/冲突/推断项，见下 |
| `pending_human` | array | 需人工确认项，`{"item","reason"}` |
| `change_log` | array | `{"at","action","reason"}`，记录骨架→成品增量 |
| `errors` | array | 读取/解析错误记录 |
| `degradations` | array | 降级说明（缺软件/缺格式等） |
| `constraints` | object | `period_months`/`budget`/`personnel`/`data_scope`，供可行性校验 |

## 2. 七要素（proposal）

### background（研究背景与缺口）
`{"known":[claim...], "unknown":[string...], "gap":claim}`

- `known`：材料中已确认的结论，逐条挂 `sources`。
- `unknown`：尚未回答/待验证的问题。
- `gap`：由 known→unknown 推出的缺口陈述，挂 `sources`。

### research_questions（研究问题，1–3 个）
每个元素 `{"question":string, "falsifiable_criterion":string, "sources":[...]}`。
`falsifiable_criterion` 必须写出"何种结果会推翻该问题"（立得住）。

### objectives（研究目标）
每个元素 `{"objective":string, "answer_to":"Q1|Q2|Q3", "metrics":[string], "sample":string, "control":string, "timepoint":string, "sources":[...]}`。
`answer_to` 指向 research_questions 的序号，保证目标与问题一一对应（对得上）。

### technical_route（技术路线）
每个元素 `{"step_id":"R1", "step":string, "input":string, "output":string, "depends_on":["R0"], "sources":[...]}`。
`depends_on` 引用其他 step_id，构成有向无环依赖；不得悬挂。

### phase_plan（阶段计划与里程碑）
每个元素 `{"phase":string, "milestone":string, "period":"M1-M3", "resources":string, "depends_on":[]}`。
`period` 为相对月份（`M1-M3`/`Q1-Q2`/`第1-3个月`/`YYYY.MM-YYYY.MM`），脚本解析后与 `constraints.period_months` 比对（排得开）。

### risks（风险与备选方案）
每个元素 `{"category":string, "risk":string, "trigger":string, "impact":string, "mitigation":string, "decision_point":string}`。
`category` 词表：`数据获取 | 方法可行性 | 进度 | 伦理与合规 | 资源与人员 | 其他`；每条必须有 trigger（触发信号）、mitigation（备选方案）、decision_point（决策时点）（说得清）。

### expected_outcomes（预期成果与考核方式）
每个元素 `{"outcome":string, "assessment":string, "sources":[...]}`。`assessment` 写出可考核方式。

## 3. source 结构（可核验）

```json
{"id":"s1", "path":"materials/xx.md", "locator":"#p3", "quote":"逐字片段"}
```

- `locator`：页码/段落/节定位；`quote`：材料逐字片段。
- 可内联挂到 claim 的 `sources` 数组，也可只给 `id` 引用顶层 `sources`。
- `kind` 词表：`fact`（原文可指认）/ `summary`（多段归纳）/ `inference`（Agent 补出）/ `conflict`（材料冲突）/ `missing`（缺口）。

## 4. uncertainty 元素

`{"item":string, "issue":string, "kind":"missing|conflict|inference|out_of_scope", "action":string}`。
所有 Agent 推断、材料冲突、不可解析项都必须在此登记，且不改变 status 之外的正式结论。
