# result.json 字段规格

本文是 `/app/output/result.json` 的完整字段规格，也是 `build` 与 `selfcheck` 的校验依据。

## 顶层结构

```json
{
  "case_id": "原样保留 case.json 的 case_id",
  "<output_contract 的全部键>": "原样回填，见下",
  "status": "ok|partial|insufficient_evidence|input_error",
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "errors": [],
  "degradations": [],
  "changes": [],
  "change_log": [],
  "runtime": {},
  "result": {"findings": [], "unchecked_items": [], "not_applicable": [], "coverage": {}, "verdict_summary": {}}
}
```

- `case_id`：不得丢失。若 `case.json` 无 `case_id`，则写 `null`。
- `output_contract`：若为 dict，**逐键原样回填**（不改名、不删除）；未知键写 `null`（键名含
  `list/items/findings/errors/warnings/notes/sources/unchecked/references/evidence/changes/records/logs/results`
  等数组暗示时写 `[]`）。嵌套 dict 递归回填，嵌套 list 写 `[]`。若 contract 本身是 list，
  统一放入 `"output_contract": []` 并在 `degradations` 说明。
- 信封字段 `status/sources/uncertainty/pending_human/errors/degradations/changes/change_log/runtime`
  由脚本生成，**覆盖**同名的 contract 键（信封优先）。

## status 取值

- `ok`：六类 facet 中与本稿相关的都已查并给出结果。
- `partial`：部分完成，缺口写在 `unchecked_items`/`pending_human` 并说明原因。
- `insufficient_evidence`：材料缺失/不可用（空输入、坏 PDF 无文本层、图表无 OCR 等）。
- `input_error`：坏 JSON 等输入错误，无法继续。

原则：不确定就 `partial` + 缺口，**不编造数据/引用/DOI/结论**。

## sources（元素）

```json
{"source_id": "D1", "path": "相对/绝对路径", "kind": "pdf|docx|text|image|other",
 "chars": 1234, "pages": 3, "problems": ["..." ]}
```

## result.findings[] 单条 finding

```json
{
  "finding_id": "F01",
  "facet": "fact_check|citation_support|method_design|statistics_report|argument_chain|academic_norms",
  "severity": "critical|major|minor|suggestion",
  "issue_type": "factual_error|insufficient_evidence|standards_issue|suggestion",
  "verdict": "confirmed|to_review|not_checked",
  "claim": "本文论断/问题描述",
  "location": "p3#para2（或 p1#para7）",
  "quote": "逐字原文（可跨行，不跨段）",
  "rationale": "判断依据",
  "revision_suggestion": "可执行修改建议",
  "confidence": "high|medium|low",
  "evidence": [{"source_id": "D1", "locator": "p3#para2", "quote": "..."}]
}
```

脚本在校验时**追加**（Agent 不必手填，也不应伪造）：
- `quote_hash`：`quote` 规范化后的截断 SHA-256（16 位十六进制）。
- `quote_verified`：布尔，quote 是否在来源索引中逐字命中。
- `quote_locator`：命中段落的 locator。
- 每个 `evidence` 项追加 `quote_hash`、`verified`、`matched_locator`。

**硬规则**：`confirmed` 且 `quote_verified == false` 的 finding，`build` 自动降级为
`to_review` 并写入 `uncertainty`。任何关键结论必须有 locator + 逐字 quote。

## result.unchecked_items[] / not_applicable[]

```json
{"facet": "citation_support", "reason": "材料无参考文献，无法核验"}
```

六类 facet 中，与本稿相关但没查的进 `unchecked_items`；确实不适用的进 `not_applicable`。

## result.coverage

六键映射：`{fact_check, citation_support, method_design, statistics_report, argument_chain, academic_norms}`
取值 `checked | unchecked | not_applicable`。由 `build` 从 findings/unchecked/not_applicable 确定性计算。

## result.verdict_summary

```json
{"overall_verdict": "pass|needs_revision|reject",
 "counts": {"total": 8, "by_severity": {...}, "by_facet": {...}, "by_verdict": {...}}}
```

overall_verdict 规则（确定性）：
- 存在 `critical` 且 `confirmed` → `reject`
- 否则存在 `major` 且 `confirmed`，或存在 `critical` 且 `to_review/not_checked` → `needs_revision`
- 否则 → `pass`

## 信封字段

- `uncertainty`：`[{"item","reason","affects"}]`；含自动追加的"quote 未逐字命中"项。
- `pending_human`：需人复核的项 `[{"item","reason"}]`（OCR、缺参考文献等）。
- `errors`：`[{"file"|"stage","detail"}]`。
- `degradations`：字符串列表，记录解析失败/超长跳过/坏编码等。
- `changes`：`[{"field","from","to"}]`，由 build 对比上一版 result.json 的易观察字段生成。
- `change_log`：追加式 `[{"ts","stage","action","detail"}]`，init/build 各记一条。
- `runtime`：`{"seconds": 数字, "generated_at": ISO 时间}`。易变量只进这里，
  findings/counts/coverage 必须确定性（同一输入重复运行结构一致）。
