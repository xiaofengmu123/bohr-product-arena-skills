# 输出契约与字段语义（开题报告与研究计划书）

本文件定义 `result.json` 的字段、七要素结构、证据结构、状态词表与对齐/可行性规则；`scripts/proposal.py check` 据此校验，`references/checklist.md` 据此自检。他人可据此复用同一套契约。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 与 case.json 的 `case_id`/`id` 一致 |
| `status` | string | 是 | `complete` / `partial` / `insufficient_evidence` / `input_error` / `failed` |
| `title` | string | 是 | 开题方案标题 |
| `summary` | string | 是 | 一段摘要，末尾可附 `limitations` |
| `genre` / `audience` / `format` | string | 否 | 体裁 / 受众 / 交付格式（来自 case.json 约束） |
| `constraints` | object | 否 | 周期 `cycle`、经费 `funding`、数据 `data`、人员 `personnel`、考核指标 `metrics` |
| `seven_elements` | object | 是 | 七要素（见 §2） |
| `evidence_map` | array | 是 | 证据清单（见 §3） |
| `artifacts` | array | 是 | 可编辑产物清单（见 §4） |
| `sources` | array | 是 | 材料清单：`{id, path, kind}` |
| `uncertainty` | array | 是 | `{item, issue, kind, action}`；kind ∈ `missing`/`conflict`/`inference`/`infeasible`/`out_of_scope` |
| `pending_human` | array | 是 | 须人工裁决：`{item, question, why}` |
| `change_log` | array | 是 | `{at, action, reason}`，须能还原“骨架→成品”增量 |

**契约回填规则**：若 case.json 提供 `output_contract`（或 `output_schema`/`required_fields`/`contract`/`fields`），则按其字段名逐一填写、缺值给 `null`/`[]`；不改名、不删未知键。无契约时用上述默认字段。

## 2. seven_elements 七要素

- `background_gap`：`{text, known[], unknown[], to_verify[], evidence[]}`——三段落（已知/未知/待验证）指向缺口，而非文献罗列。
- `research_questions[]`：`{id, text, falsifiable, metrics[], sample, control, timepoint, evidence[]}`——1–3 个可证伪问题，每个给出指标/样本/对照/时间点。
- `objectives[]`：`{id, question_ref, text, criteria[], evidence[]}`——`question_ref` 必须命中一个 `research_questions[].id`。
- `technical_route[]`：`{id, step, input, output, depends_on[], objective_ref, question_ref, evidence[]}`——有输入输出与前后依赖；`objective_ref`/`question_ref` 至少一个可回查。
- `schedule[]`：`{id, phase, month_start, month_end, milestone, depends_on[], evidence[]}`——按月份排布，标关键路径与可并行部分。
- `risks[]`：`{id, category, description, trigger, impact, mitigation, decision_point, evidence[]}`——category ∈ `数据获取`/`方法可行性`/`进度`/`伦理与合规`；每条必须有触发信号、影响范围、备选方案、决策时点。
- `expected_outcomes[]`：`{id, objective_ref, outcome, assessment, evidence[]}`——`objective_ref` 必须命中一个 `objectives[].id`；`assessment` 写考核方式。

**对齐规则（对得上）**：目标→问题、路线→目标/问题、成果→目标、验证→问题，四类 `ref` 均须可回查；`check` 对失配输出 FAIL。

**可行性规则（排得开）**：`constraints.cycle` 为数值时，任一 `schedule[].month_end` 超过周期即 FAIL；指标/样本/经费矛盾写入 `uncertainty`（kind=infeasible）。

## 3. 证据结构（说得清）

`evidence_map[]` 每项：`{id, locator, quote, claim_refs[]}`。

- `locator`：材料内定位，如 `materials/paper.md#p3` 或 PDF 页码 `paper.pdf#page=4`，必填。
- `quote`：材料**逐字**片段，必填且须与材料一致，不得改写。
- `claim_refs[]`：该证据支撑的要素 id（如 `["RQ1", "O1"]`）。
- 七要素中每个 `evidence[]` 引用的 id 必须存在于 `evidence_map`；`check` 对悬空引用输出 FAIL。
- 事实/归纳/推断三分：`fact`=原文可指认，`summary`=多段归纳（仍需给 locator+quote），`inference`=Agent 补出——必须在 `uncertainty`（kind=inference）说明。

## 4. artifacts 清单

每项 `{name, path, kind}`；`kind` ∈ `editable`/`preview`。由 `scripts/proposal.py render` 生成：

- `proposal.md`（editable）：七要素完整方案，保留章节编号与结构。
- `evidence_map.md`（editable）：证据映射表（id/locator/逐字 quote/支撑项）+ 材料清单。
- `schedule.mmd`（preview）：Mermaid 甘特图。
- `preview.svg`（preview）：自包含 SVG 预览，无需额外软件。

题目要求 docx/pptx/xmind 时，用运行时 office-renderer 生成并在 `artifacts` 附真实渲染的 PDF/PNG 预览；不可用时按 SKILL.md 降级为 Markdown。

## 5. 确定性与容错

- 脚本仅依赖标准库，无随机数，固定排序；同一输入两次运行产物一致。
- 空输入/坏 JSON/坏编码/缺文件不崩溃：`init` 仍写合法 partial 骨架，`check`/`render` 输出诊断并返回非零。
- 单材料文件 >2MB 跳过并记录，不整包失败。
