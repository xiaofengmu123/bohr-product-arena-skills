# 输出契约与字段语义（references/contract.md）

本文件定义 `result.json` 的字段语义、七要素 schema、词表与 evidence/source 结构。SKILL.md 是入口，本文件是字段与词表的唯一权威；两者不一致时以本文件为准。

## 1. result.json 顶层结构

| 字段 | 类型 | 含义 |
|-|-|-|
| `case_id` | str | 与 case.json 的 `case_id`/`id` 一致 |
| `status` | str | `complete` / `partial` / `failed`（诚实标注，缺数据不得 complete） |
| `title` | str | 开题方案标题 |
| `summary` | str | 一段式摘要：问题 + 目标 + 方法 + 预期 |
| `proposal` | str | 可直接编辑的 Markdown 正文（保留章节编号，见 `proposal.md`） |
| `elements` | object | 七要素结构化对象，schema 见第 2 节 |
| `artifacts` | list | 可编辑产物相对 `/app/output` 的路径列表 |
| `sources` | list | 证据清单，结构见第 3 节 |
| `uncertainty` | list | 不确定/缺口/冲突/推断项，结构见第 4 节 |
| `pending_human` | list | 需人工裁决项，结构见第 4 节 |
| `change_log` | list | 修改记录，结构见第 5 节 |
| `runtime` | object | 运行元数据（预算、起始时间、contract_fields），由 `init` 写入 |

默认契约字段即上表除 `case_id`/`status`/`runtime` 外的全部字段。若 case.json 声明了 `output_contract`，则以其声明的键为准原样回填（缺值给 `null`/`[]`，不改名、不删键），并额外追加信封字段。

## 2. elements 七要素 schema

```json
{
  "background": {
    "known":     [{"claim": "", "source_ids": ["S1"]}],
    "unknown":   [{"claim": "", "source_ids": []}],
    "to_verify": [{"claim": "", "source_ids": []}],
    "gap_statement": "",
    "constraints": [{"item": "", "source_ids": []}]
  },
  "questions": [
    {"id": "Q1", "question": "", "falsifiable_test": "", "rationale": "", "source_ids": []}
  ],
  "objectives": [
    {"id": "O1", "question_id": "Q1", "objective": "", "metric": "", "sample": "",
     "control": "", "timepoint": "", "threshold": "", "threshold_basis": "", "source_ids": []}
  ],
  "route": [
    {"id": "T1", "name": "", "input": "", "output": "", "method": "", "depends_on": []}
  ],
  "plan": {
    "constraints": {"period": "", "budget": "", "personnel": "", "data": ""},
    "milestones": [
      {"id": "M1", "time": "", "tasks": "", "deliverable": "", "resources": "", "depends_on": []}
    ]
  },
  "risks": [
    {"id": "R1", "category": "", "trigger_signal": "", "impact_scope": "", "fallback": "", "decision_point": ""}
  ],
  "outcomes": [
    {"id": "E1", "type": "", "description": "", "assessment_method": "", "target": ""}
  ]
}
```

### 填写规则

- `questions` 数量 1–3；每个必须有 `falsifiable_test`（可证伪判据）。
- `objectives[].question_id` 必须指向存在的 `questions[].id`；`threshold` 或 `threshold_basis` 至少一项，且依据材料（挂 source_ids）。
- `route[].depends_on` 与 `plan.milestones[].depends_on` 引用同表内 id，表达前后依赖；`plan.constraints` 必须填写周期/经费/人员。
- `risks[].category` 取值见词表；四字段（trigger_signal / impact_scope / fallback / decision_point）缺一不可。
- 每个事实或归纳论断都要能通过 `source_ids` 回溯到 `sources`。

## 3. source 结构（证据溯源）

```json
{"id": "S1", "locator": "materials/paper.pdf#p3 或 notes.md#L12",
 "quote": "逐字片段……", "claims": ["Q1", "B2"],
 "kind": "fact|summary|inference", "note": ""}
```

- `locator`：材料路径 + 页码/段落/行号定位，缺失时必须用 `quote` 兜底。
- `quote`：材料中的逐字片段；`summary`/`inference` 类允许为归纳，但须在 `note` 说明归纳范围。
- `kind`：`fact`=原文可指认；`summary`=多段归纳；`inference`=Agent 补出（必须同时写入 `uncertainty`）。
- 禁止新增材料外事实；引用无法获取时写 `pending_human`（not_reported），不伪造 DOI/页码。

## 4. uncertainty 与 pending_human

```json
{"item": "阈值", "issue": "材料未给阈值依据", "kind": "missing|conflict|inference|out_of_scope|input_error",
 "evidence": "S1,S2", "action": "人工确认"}
```

```json
{"item": "周期", "question": "6 个月是否含伦理审批？", "why": "影响里程碑排布", "impact": "阶段计划"}
```

- `uncertainty.kind`：`missing` 缺材料、`conflict` 信息矛盾、`inference` 推断、`out_of_scope` 超出范围、`input_error` 输入异常。
- `pending_human` 记录需要人裁决、且影响结论的问题，不擅自选一个版本。

## 5. change_log 结构

```json
{"at": "2026-11-24T00:00:00+00:00", "action": "fill questions", "reason": "步骤 2 回填七要素"}
```

`at` 用 ISO-8601；`action` 记录动作，`reason` 记录原因，可还原"从骨架到成品"的增量过程。

## 6. 词表

- 风险类别（`risks[].category`）：`data`（数据获取）、`method`（方法可行性）、`schedule`（进度）、`ethics`（伦理与合规）。
- 七要素键：`background`、`questions`、`objectives`、`route`、`plan`、`risks`、`outcomes`。
- 状态（`status`）：`complete`、`partial`、`failed`。
- 证据类型（`sources[].kind`）：`fact`、`summary`、`inference`。
