# 输出契约与字段规范（output-contract）

本文件定义 `/app/output/result.json` 的完整字段、七要素结构与词表。`scripts/main.py` 的 `init/check/render` 与此规范保持一致；他人可按此规范复用本 Skill。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `case_id` | string | 是 | 与 case.json 的 `case_id`/`id` 一致 |
| `status` | string | 是 | 枚举：`complete` / `partial` / `insufficient_evidence` / `failed` |
| `title` | string | 是 | 方案标题，取自任务/底稿或据材料拟定 |
| `summary` | string | 是 | 一段式摘要：问题、方法、预期，与七要素一致 |
| `proposal` | object | 是 | 七要素结构（见 §2） |
| `evidence_map` | array | 是 | 事实与来源映射（见 §3） |
| `boundary` | object | 是 | 版本与边界（见 §4） |
| `artifacts` | array | 是 | 产物相对路径（相对 `/app/output`），文件须真实存在 |
| `sources` | array | 是 | 证据清单（见 §3） |
| `uncertainty` | array | 是 | 不确定/待确认项（见 §5） |
| `pending_human` | array | 是 | 需人工裁决项（见 §5） |
| `change_log` | array | 是 | 修改记录，`{at, action, reason}` |
| `output_contract_echo` | array | 否 | 回显从 case.json 识别的契约字段名，便于复核 |

`case.json` 若提供 `output_contract`（或 `output_schema`/`required_fields`/`contract`），其字段名与必填标记优先，脚本逐项回填；未声明时使用本规范的默认必填字段。未知契约字段不改名、不删除，缺值给 `null`/`[]`。

## 2. 七要素（proposal）

题目评分核心是七要素齐全、与周期资源相容、目标—路线—验证—成果一一对应。每项结构与字段如下：

1. `background`：`{known: [str], unknown: [str], to_verify: [str], gap_statement: str}`——“已知—未知—待验证”缺口陈述，而非文献罗列。
2. `problem_definition`：`{questions: [{id, question, falsifiable_test, gap_evidence: [source_id], assumptions: [str]}]}`——1–3 个可回答、可证伪的研究问题。
3. `objectives`：`[{id, question_ref, objective, success_judgement, criteria: {metrics, sample, control, timepoint}}]`——每目标对应问题，含可考核判断标准。
4. `technical_route`：`{steps: [{id, name, input, output, method, depends_on: [id], parallelizable: bool}], critical_path: [id], resources: {}}`——步骤带输入输出与前依赖，标注关键路径与可并行部分。
5. `schedule`：`{cycle: str, milestones: [{id, month, deliverable, workload, depends_on: [id]}]}`——按周期（季度/月份）排布，里程碑与工作量匹配给定周期、经费、数据、人员。
6. `risks`：`[{id, category, description, trigger_signal, impact, mitigation, decision_point}]`——`category` 取词表：`数据获取` / `方法可行性` / `进度` / `伦理与合规` / `其他`；每条必须有触发信号、影响、备选方案、决策时点。
7. `expected_outcomes`：`[{id, question_ref, outcome, assessment_method, target}]`——每项成果对应问题并写明考核方式。

对齐关系（对得上）：`question_ref` 把目标/成果挂到问题；`depends_on` 把路线步骤与里程碑串成前后依赖；`critical_path` 指明关键路径。

## 3. 证据溯源（sources 与 evidence_map）

- `sources` 条目：`{id, path, locator, quote, kind}`。`locator` 如 `materials/paper.md#p3`（文件#页/段落），`quote` 为逐字原文；二者至少其一，关键论断建议两者都有。
- `evidence_map` 条目：`{claim_id, claim, source_id, locator, quote, status}`。`source_id` 必须能在 `sources` 中找到；`status` 可取 `fact`（原文可指认）/ `summary`（多段归纳）/ `inference`（Agent 推断，须列入 uncertainty）。
- 每个关键数字、术语、引用、图表、论断都要能逐条回溯；不新增材料外事实，不虚构引用/DOI/数值。

## 4. 版本与边界（boundary）

`{changes: [{from, to, reason}], material_gaps: [{item, issue, action}], exceptions: [str]}`——记录修改差异、材料缺口与异常；未确认部分另入 `pending_human` 并明确标注。

## 5. 不确定与人工确认

- `uncertainty` 条目：`{item, issue, kind, action}`；`kind` 取 `missing` / `conflict` / `inference` / `out_of_scope` / `not_reported`。
- `pending_human` 条目：`{item, question, why}`——信息冲突或关键裁决需人工时记录。
- 材料缺失不补造；信息矛盾并列呈现并标 `conflict`，建议人工裁决。
