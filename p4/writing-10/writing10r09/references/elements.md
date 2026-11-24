# 字段定义、意见分类法与回复模板

本文件是 `SKILL.md` 输出契约的权威定义。三个 output_contract 字段为 `deliverable`、`fact_source_mapping`、`version_boundary`；其余为诚实运行记录。

## 1. result.json 字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 从 `/input/case.json` 原值读取，不得改 |
| `status` | string | `in_progress\|complete\|partial\|failed\|insufficient_evidence\|input_error`；最终为后四者之一 |
| `deliverable` | object | `{path, format, title, status, files[]}`；files 每项 `{role, path, format}`，role ∈ revised_manuscript / response_letter / revision_ledger / evidence_checklist |
| `fact_source_mapping` | array | 每项 `{claim, source_ref, location, quote, status}`；status ∈ traced / assumption / unverified / modified |
| `version_boundary` | object | `{changes[], pending_human[], material_gaps[], anomalies[]}` |
| `sources` | array | `{id, type, ref, description}`；ref 用 `/input` 内路径或可复核定位 |
| `uncertainty` | array | `{item, reason, disposition}` |
| `change_log` | array | `{ts, action, detail}`；每次落盘/修改追加一条 |
| `comments` | array | 拆解后的意见（见下） |
| `revision_ledger` | array | 改动台账（见下） |

## 2. 意见分类法（comments）

每条意见拆为：`{id, type, severity, sub_requirements[], decision, tone, action, raw_quote, source_ref}`。

- `type`：`method`（方法）/ `data`（数据）/ `writing`（写作）/ `format`（格式）/ `ethics`（伦理）。
- `severity`：`blocking`（必须解决）/ `major` / `minor` / `discretionary`（可选）。
- `sub_requirements[]`：一条意见里的多个子要求，逐条给出处理结论，禁止只答一半。
- `decision`：`accept`（采纳）/ `partial`（部分采纳）/ `reject`（不采纳）；`reject` 必须给证据性理由。
- `tone`：`accept`（接受）/ `clarify`（澄清）/ `polite-rebut`（礼貌反驳）/ `appeal`（申诉）；反驳不得冒犯，接受不得无原则顺从。
- `action`：落到正文的具体动作（改哪节、加哪句、补哪项分析）。
- `raw_quote`：意见原文逐字片段（有则必填），`source_ref` 指向 reviewer-comments 路径。

## 3. 改动台账（revision_ledger）

每条 `{comment_id, decision, tone, sub_requirements[], action, location, before, after, status, evidence}`。

- `location`：`{file, section, anchor, line}`；`anchor` 是改后正文中可全文检索的原文锚点（改稿后仍有效）。
- `before` / `after`：改前/改后逐字片段，供 diff 核对。
- `status`：`done`（已落稿）/ `partial` / `not_done` / `not_needed`。
- `evidence`：需补实验/数据/文献/重做分析时 `{type, item, status, source_ref, alternative}`；`status` ∈ done / partial / pending / not_doable；`not_doable` 必须写替代说明，禁止伪称已补做。

## 4. 语气四档

| 语气 | 适用 | 措辞要求 |
|-|-|-|
| accept | 意见正确且已改 | "已按意见修改，见 §X 锚点Y" |
| clarify | 意见源于误解 | "我们澄清……原稿第X段实际表明……" |
| polite-rebut | 意见有误或与事实不符 | "谨指出……与材料中的Z不一致" |
| appeal | 需争取、证据不足时 | 承认不确定性，给出已有证据与下一步 |

## 5. 逐条回复信模板（response_letter.md）

```text
# 回复信（草稿，非正式提交）
日期/版本：<manuscript_version>
逐条回复（意见号 → 结论 → 修改定位 → 证据/替代说明）：

## 意见 1（<type> / <severity>）
- 结论：<accept|partial|reject>（<tone>）
- 子要求覆盖：<逐条>
- 修改定位：<file> §<section> 锚点 `<anchor>`（第 <line> 行）
- 证据/替代：<evidence_checklist 编号或"不适用">
- 回复正文：……
```

## 6. 定位锚点约定

- 锚点须是改后稿件中的**连续逐字片段**（6–40 字为宜），避免"改后失效"；不要用会被后续编辑破坏的绝对行号作唯一依据。
- 每条 accept/partial 且 status=done 的改动，必须有一个可在 `revised_manuscript.md` 中全文检索到的 `anchor`。
- 多轮返修时，`manuscript_version` 每次递增，`revision_ledger` 保留历史承诺；回复信引用的定位必须指向当前版本。
