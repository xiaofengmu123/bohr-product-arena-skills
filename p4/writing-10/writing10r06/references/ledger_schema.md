# 台账 Schema 与字段规则（中间表示）

本 Skill 的核心是“台账先行”：先产出三份结构化中间产物，再用它们生成修订稿与回复信。
三份台账都放在 `/app/output/` 下，与正文一起交付，使“已修改”“已补证据”可被独立复核。

## 1. review_ledger.json（意见台账）

顶层字段：`schema_version`、`generated_at`、`comments_ref`、`baseline_ref`、`parse_note`、`comments[]`。

`comments[]` 每条意见一个对象：

| 字段 | 类型 | 规则 |
|-|-|-|
| `comment_id` | string | 稳定编号，如 `R1`、`R2.3`；跨轮次不变，多轮返修沿用同一编号。 |
| `raw_quote` | string | 从意见材料逐字摘录；不得改写，缺失时写 `""` 并在 `parse_note` 说明。 |
| `source_line` | number | 意见材料中的行号（可省略）。 |
| `type` | enum | `method / data / writing / format / ethics / unknown`。 |
| `severity` | enum | `blocking / major / minor / unknown`。 |
| `sub_requirements[]` | array | 一条意见内的多个子要求，每项 `{sub_id, requirement, status}`；`status ∈ addressed/partial/open`。 |
| `decision` | enum | `accept / partial / reject / clarify / open`（open 表示尚未决断）。 |
| `tone` | enum | `accept / clarify / polite_refute / appeal`。 |
| `response_summary` | string | 该条回应要点（正文由之展开）。 |
| `revision_refs[]` | array | 指向 `revision_map.changes[].change_id`。 |
| `evidence_refs[]` | array | 指向 `evidence_ledger.evidence[].evidence_id`。 |
| `status` | enum | `addressed / partial / open / blocked`。 |

约束：
- `decision ∈ accept/partial` 时，必须至少有一个 `revision_refs` 或 `evidence_refs`，且 `status ≠ open`。
- `decision = open` 时不得先写 refs；先决断再链接。
- 一条意见包含多个子要求时，全部拆入 `sub_requirements[]`，回应与链接必须覆盖每个子要求（“答得全”）。

## 2. revision_map.json（修改定位台账）

顶层：`schema_version`、`generated_at`、`baseline_version`、`revision_version`、`changes[]`。

`changes[]` 每处改动一个对象：

| 字段 | 类型 | 规则 |
|-|-|-|
| `change_id` | string | `CHG-<n>`，回复信中引用它。 |
| `comment_ids[]` | array | 该改动回应哪些意见。 |
| `target` | string | 被改文件（`materials/manuscript-baseline.md` 等）。 |
| `section` | string | 章节名。 |
| `anchor` | string | 可检索原文锚点：章节标题、段落首尾短语或行号。 |
| `before_quote` | string | 修改前逐字片段（用于 diff 核对）。 |
| `after_quote` | string | 修改后逐字片段。 |
| `locator` | string | `版本:章节:段落/行号`，改稿后仍有效。 |
| `status` | enum | `done / partial / not_done / blocked`。 |
| `note` | string | 可选说明。 |

约束：
- `status = done` 必须同时有 `locator` 与至少一个 `before_quote/after_quote`；否则自检脚本报错。
- 回复信中每处 `CHG-x` 引用都必须对应 `status = done` 的条目（“改得准”）。
- 一个 `change_id` 可回应多条意见（`comment_ids` 多值），但不得让“已修改”指向不存在或未完成的改动。

## 3. evidence_ledger.json（证据清单）

顶层：`schema_version`、`generated_at`、`evidence[]`。

`evidence[]` 每条证据请求一个对象：

| 字段 | 类型 | 规则 |
|-|-|-|
| `evidence_id` | string | `E-<n>`，回复信中引用它。 |
| `comment_ids[]` | array | 该证据回应哪些意见。 |
| `kind` | enum | `experiment / data / literature / analysis`。 |
| `request` | string | 审稿人要求补什么。 |
| `status` | enum | `in_place / planned / not_possible / partial`。 |
| `source_ref` | string | 数据/文献出处或可复核定位。 |
| `fallback` | string | `not_possible` 时必填：为何无法补做 + 替代说明。 |
| `note` | string | 可选。 |

约束：
- `status = not_possible` 必须写 `fallback`（“证得住”：说清为何无法补做）。
- 不得把“无法补做”伪造成“已补做”；不得冒称真实补做实验。

## 4. 定位（locator）约定

- 版本用 `baseline-1` / `revision-1`，多轮为 `revision-2` 等；跨轮引用必须带版本。
- `locator` 形如 `revision-1:Methods:para1` 或 `revision-1:Figure 3:legend`；段落号按该章节自 1 起。
- `anchor` 尽量取“改稿前后都不变”的稳定短语（章节标题、编号、固定术语），使定位在改稿后仍有效。

## 5. 语气四档（tone）

- `accept`：明确采纳，落到具体动作。
- `clarify`：澄清误解，不改结论。
- `polite_refute`：礼貌反驳，给出证据与理由。
- `appeal`：申诉（争议裁决），语气克制、举证充分。
禁止冒犯性表达、过度承诺、无原则顺从。

## 6. result.json 的 fact_source_mapping 规则

每项 `{claim, source_ref, locator, quote, status}`：
- `status = traced`：能从材料中逐字定位（`locator` + `quote`）。
- `status = assumption`：显式假设，来自缺材料后的人为约定，须在 `version_boundary.material_gaps` 登记。
- `status = unverified`：来源存在但本次未能核验（如 PDF 解析失败）。
数字、术语、引用、图表、关键论断至少各一项；不新增材料外事实。
