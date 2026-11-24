# 字段与规则说明（elements）

本 Skill 的核心中间表示是 `revision_ledger.json`（回应台账），它是回复信、修改稿、
定位表、证据清单的唯一事实来源。`result.json` 从台账与输入材料合成，供评分人复核。

## result.json 字段

| 字段 | 含义 | 规则 |
|-|-|-|
| `case_id` | 案例标识 | 从 `case.json` 原样读取，缺失时为 `unknown` |
| `status` | 运行状态 | `in_progress` 仅允许作为前 3 分钟骨架；最终为 `complete` / `partial` / `insufficient_evidence` / `input_error` |
| `deliverable` | 目标产物 | 含 `primary`、`path`、`format`、`title`、`status` 与 `artifacts`（四个子产物：`revised_manuscript`、`response_letter`、`revision_locations`、`evidence_ledger`） |
| `fact_source_mapping` | 事实与来源映射 | 数组，每项 `{claim, source_ref, location, status}`；`status` ∈ `traced` / `unverified` / `assumption` |
| `version_boundary` | 版本与边界 | `{changes[], pending_confirmation[], material_gaps[], anomalies[]}` |
| `sources` | 来源清单 | 数组 `{id, type, ref, description}`，`ref` 为 `/input` 内路径 |
| `uncertainty` | 不确定性 | 数组 `{item, reason, disposition}` |
| `pending_human` | 需人工确认项 | 数组，逐条写明待确认内容 |
| `errors` / `degradations` | 错误与降级 | 数组，如实记录，不编造软件状态 |
| `runtime` | 运行信息 | `{started_at, finished_at, requests_used, seconds_used}` |
| `change_log` | 变更记录 | 数组 `{ts, action, detail}`，每次落盘/修改追加一条 |

题目 output_contract 的三个原始字段是 `deliverable`（目标产物）、
`fact_source_mapping`（事实与来源映射）、`version_boundary`（版本与边界）；三者必须
回填，未知值给 `null`/`[]`，不改名、不删除未知键。

## revision_ledger.json 字段（台账）

每条意见（`comments[]`）字段：

- `id`：`C1`、`C2`…，稳定编号，用于跨文档对齐。
- `ref`：原始意见标签（如 `Reviewer 1, Comment 2`）。
- `raw_text`：意见摘要原文。
- `sub_requirements[]`：`{id, text, type, disposition, note}`；`type` ∈
  `method|data|writing|format|ethics|other`；`disposition` ∈
  `addressed|waived|deferred|rejected`。
- `decision`：`accept|partial_accept|reject|clarify|appeal`。
- `tone`：`accepting|clarifying|polite_rebuttal|appeal`（与 `decision` 对齐）。
- `response`：逐条回复正文（由 Agent 撰写）。
- `rationale`：决策理由。
- `actions[]`：`{verb, detail}`，`verb` ∈ `modified|added|clarified|rebutted|waived`。
- `locations[]`：`{section, before, after, anchor_status}`；`before` 为改前逐字摘录，
  `after` 为改后逐字摘录；`anchor_status` ∈ `verified|unverified`。
- `evidence[]`：`{item, status, source, note}`；`status` ∈
  `provided|not_provided|waived|pending`。

## 定位锚点（不随编辑漂移）

不使用页码/行号（改稿后失效）。每个修改位置以「改前逐字摘录 + 章节名 + 段序号」为
稳定锚点；`check_alignment` 会核验 `before` 存在于基线稿、`after` 存在于修改稿。
两者同时命中才标 `verified`，否则标 `unverified` 并写入 `anomalies`。

## 事实与来源规则

- 每条数字、术语、引用、关键论断至少一条 `fact_source_mapping`。
- 引用公开论文/报告链接只作为「来源身份」，不表示已联网核验；不得冒称真实审稿人
  逐字要求、真实补做实验、原作者已改稿或编辑部已接收。
- 冲突信息不隐藏：同一指标两处不一致时都记录并标 `source_conflict`。
- 缺失即写 `not_reported` 或 `pending_human`，不虚构引用、DOI、数值。

## 意见分类法（taxonomy）

| 维度 | 取值 | 用途 |
|-|-|-|
| `type` | `method` / `data` / `writing` / `format` / `ethics` / `other` | 意见拆解后的子要求类型 |
| `decision` | `accept` / `partial_accept` / `reject` / `clarify` / `appeal` | 采纳程度 |
| `tone` | `accepting` / `clarifying` / `polite_rebuttal` / `appeal` | 语气强度，须与 `decision` 对齐 |
| `disposition` | `addressed` / `waived` / `deferred` / `rejected` | 每条子要求的处理结论 |
| `evidence.status` | `provided` / `not_provided` / `waived` / `pending` | 补实验/数据/文献的到位状态 |

一条意见含多个子要求时，每条子要求单独记 `type` 与 `disposition`，避免只答一半。

## 回复信模板（可复用）

每条意见在回复信中按固定块输出，保证可核验：

```text
## {comment_id} — {ref}
- Decision: {decision} | Tone: {tone}
Sub-requirements:
- {id} [{type}] {text} — disposition: {disposition}
Response:
{response 正文，语气与 tone 一致，避免空话/过度承诺}
Actions:
- {verb}: {detail}
Locations:
- {section}: "{before}" → "{after}" [{anchor_status}]
Evidence:
- {item}: {status} — {source} ({note})
```

## 最小调用示例（示意，非正式案例结果）

输入目录结构：

```text
/input/case.json                     # {"case_id": "demo-1", ...}
/input/task.md
/input/materials/manuscript-baseline.md
/input/materials/reviewer-comments.md
```

调用（Skill 根目录 `/skills/weekly-skill`）：

```bash
python scripts/main.py all --input /input --out /app/output --work /app/work
python scripts/selftest.py
```

示意输出（标注为示例）：`/app/output/result.json` 含 `case_id=demo-1`、三个
output_contract 字段、`status`、`sources`、`uncertainty`、`change_log`；并在
`/app/output` 下生成 `revised_manuscript.auto.md`、`response_letter.md`、
`revision_locations.md`、`evidence_ledger.md`。正式案例结果由平台运行产生，示例不冒充。
