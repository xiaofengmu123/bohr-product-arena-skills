# result.json 输出契约规格

`/app/output/result.json` 是评测读取的主结果文件，`review_report.md` 是供人复核的
可读报告。以下为完整字段规格；字段顺序不要求固定，键名与枚举值必须一致。

## 1. 顶层信封

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string\|null | 原样保留 `case.json` 的 case_id，不得丢失 |
| `status` | enum | `ok` \| `partial` \| `insufficient_evidence` \| `input_error` |
| `output_contract` | any | `case.json` 的 output_contract 原样深拷贝 |
| 契约键（平铺） | any | output_contract 的每个键原样回填到顶层，不改名不删除，缺失写 `null` |
| `artifacts` | object | 主产物相对 `/app/output` 的路径，如 `{"review_report": "review_report.md"}` |
| `sources` | array | 材料摘要：`source_id / path / name / kind / paragraphs / problems` |
| `uncertainty` | object | 见 §3 |
| `pending_human` | array | 待人工复核项：`{item, facet, reason}` |
| `errors` | array | 运行错误：`{file\|stage, error}` |
| `degradations` | array | 降级记录：`{scope, severity, detail, fallback}` |
| `limitations` | array | 本版重点：`{id, scope, limitation, impact, mitigation}` |
| `change_log` | array | 变更记录：`{time, version, what}` |
| `runtime` | object | `{started_at, finished_at, elapsed_s, revision, command, note}` |
| `result` | object | 见 §2 |

`status` 判据：

- `ok`：六面中与本文相关的检查项均已查并有结果。
- `partial`：部分完成，`result.coverage` / `unchecked_items` 写明缺口。
- `insufficient_evidence`：材料不可用（缺文件、无文本层、无法读取）。
- `input_error`：输入坏 JSON 等。

## 2. result 主结构

```json
{
  "findings": [ { /* 见下 */ } ],
  "unchecked_items": [ { "item": "...", "facet": "...", "reason": "..." } ],
  "coverage": {
    "fact_check": { "status": "checked|partial|not_checked|not_applicable|unverifiable",
                    "reason": "...",
                    "anchors": { "FCT-1": { "status": "...", "finding_ids": ["F01"] } } }
  },
  "verdict_summary": {
    "overall": "reject|major_revision|minor_revision|accept|inconclusive",
    "counts": { "critical": 0, "major": 0, "minor": 0, "suggestion": 0 },
    "by_facet": { "fact_check": 0, "...": 0 },
    "by_issue_type": { "factual_error": 0, "...": 0 }
  }
}
```

`coverage` 必须覆盖全部六面；每个面含全部检查锚点（见 `review-checklist.md`）。
某个面 / 锚点"不适用"或"无法核验"也要显式写出原因，这是完整性与可核验性的依据。

## 3. uncertainty（本版重点：显式化不确定性）

```json
{
  "source_conflicts": [ { "topic": "...", "locations": ["para3", "para7"],
                          "note": "两处数值冲突，未擅自取舍" } ],
  "ambiguous":        [ { "location": "...", "note": "原文歧义" } ],
  "unverifiable":     [ { "scope": "...", "note": "引用/图表/软件状态无法核验" } ],
  "overall_confidence": "high|medium|low"
}
```

凡无法确认的事实、引用、图表、软件状态，一律落入上述结构或 `pending_human`，
不得用猜测补齐。

## 4. 单条 finding 形状

```json
{
  "finding_id": "F01",
  "facet": "fact_check|citation_support|method_design|statistics_report|argument_chain|academic_norms",
  "severity": "critical|major|minor|suggestion",
  "issue_type": "factual_error|insufficient_evidence|standards_issue|suggestion",
  "verdict": "confirmed|to_review|not_checked",
  "claim": "本文论断/问题描述",
  "location": "para7 或 p3#para2",
  "quote": "逐字原文（白空格归一后必须命中某一自然段）",
  "rationale": "判断依据",
  "revision_suggestion": "可执行修改建议",
  "confidence": "high|medium|low",
  "check_anchor": "MET-1（对应 review-checklist.md）",
  "uncertainty": { "kind": "none|source_conflict|ambiguous_claim|missing_support|external_unverifiable",
                   "note": "..." },
  "evidence": [ { "source_id": "D1", "locator": "para7", "quote": "..." } ]
}
```

- `quote` 是稿件原文的逐字片段，必须能被 `check` 命中；`evidence` 是被引文献 /
  数据 / 图表的支撑证据，外部文献无法核验时标 `external_unverifiable`，不得臆断。
- `verdict=confirmed` 只用于有明确依据的结论；不确定一律 `to_review`。
- `issue_type=factual_error` 只与 `confirmed` 搭配；证据不足用 `insufficient_evidence`。

## 5. 契约回填规则

`case.json` 的 `output_contract` 若为 dict，则每个键原样平铺到 result.json 顶层；
若为 list（键名或 `{name, ...}`），每个键名写入顶层且值为 `null`。随后叠加本表
信封键，信封键优先；原始契约同时保存在 `output_contract` 便于复核。
