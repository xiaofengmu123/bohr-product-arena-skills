# result.json 字段规格

`/app/output/result.json` 是最终交付物，供平台与人工复核逐条回到原文核对。顶层结构：

```json
{
  "case_id": "…",
  "<output_contract 的其他全部键原样回填>": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "errors": [],
  "degradations": [],
  "changes": [],
  "change_log": [],
  "runtime": {},
  "verdict_summary": {},
  "result": {"findings": [], "unchecked_items": []},
  "products": {"review_report": "review_report.md"}
}
```

## 1. 信封字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string\|null | 原样保留 case.json 的 case_id，不得丢失 |
| `<contract 键>` | 任意 | `output_contract` 的全部键原样回填，未知键写 `null`/`[]`，不改名不删除 |
| `status` | string | `ok` 六类相关项已查；`partial` 部分完成并写明缺口；`insufficient_evidence` 材料不可用；`input_error` 输入坏 JSON 等 |
| `sources` | list | 每个材料一个对象：`source_id/kind/name/path/size/pages/paragraphs/locator_scheme/problems` |
| `uncertainty` | list | 歧义与冲突：`{item, reason, resolution}`；冲突用 `reason="source_conflict"`，两处都记录，不擅自取舍 |
| `pending_human` | list | 需人工确认/外部核验的事项，逐条可执行 |
| `errors` | list | 解析/校验错误 `{file, error}`，如实记录 |
| `degradations` | list | 缺软件/缺文本层/超长跳过/OCR 失败等降级说明，绝不伪装成已处理 |
| `changes` | list | 本次运行相对骨架的结构性变更说明 |
| `change_log` | list | `{at, phase, change}` 增量记录，重复运行结构一致，易变量只进 `runtime` |
| `runtime` | object | `started_at/ended_at/elapsed_seconds/time_budget_seconds/request_budget/content_hash/model_requests_used`；模型请求数由平台记录，此处恒 `null`，不编造 |
| `verdict_summary` | object | `coverage`（六分面状态）、`counts`（按 severity/facet）、`overall_verdict` |
| `result` | object | `findings[]` + `unchecked_items[]` |
| `products` | object | 主产物相对路径，如 `{"review_report": "review_report.md"}` |

`status` 由脚本按事实推导：坏 case.json→`input_error`；无可读文本→`insufficient_evidence`；
六个分面（`n/a` 除外）全部 `checked`→`ok`；否则→`partial`。

## 2. 单条 finding 字段

```json
{
  "finding_id": "F01",
  "facet": "fact_check|citation_support|method_design|statistics_report|argument_chain|academic_norms",
  "severity": "critical|major|minor|suggestion",
  "issue_type": "factual_error|insufficient_evidence|standards_issue|suggestion",
  "verdict": "confirmed|to_review|not_checked",
  "claim": "本文论断/问题描述",
  "location": "D1#p3#para2 或 D1#para5（多位置用 \" / \" 连接）",
  "quote": "逐字原文片段，可跨行但不可跨段",
  "rationale": "判断依据：为什么这样判，落到原文证据",
  "revision_suggestion": "可执行修改建议",
  "confidence": "high|medium|low",
  "evidence": [{"source_id": "D1", "locator": "para5", "quote": "…"}]
}
```

| 字段 | 要求 |
|---|---|
| `finding_id` | 稳定唯一；机械预审用 `M01..`，人工 finding 用 `F01..`，同一输入重复运行保持一致 |
| `facet` | 六选一；找不到对应分面就归入最贴近的，并在 rationale 说明 |
| `severity` | 见 review-checklist.md 第 2 节 |
| `issue_type` | 见 review-checklist.md 第 3 节 |
| `verdict` | 见 review-checklist.md 第 4 节；正式 finding 不允许 `not_checked` |
| `quote` | 必须逐字取自原文；`check` 会做规范化后逐字命中校验，未命中即 ERROR |
| `location` | 必须是 `D<id>#…` 形式；图片/图表无可读文本时写所属段落 locator 并在 rationale 说明 |
| `evidence` | 补充佐证；每条含 `source_id/locator/quote`，与主 quote 一样逐字可溯 |

## 3. 可复核性要求（评分“结果可核验性”）

- 每条 finding 都能由 `location + quote + rationale` 回到原文逐字核对；
- 冲突信息两处都列，不隐藏；看不到的被引文献不臆断主张；
- `unchecked_items` 逐条写清“不适用/材料不足/时间不足”的具体原因；
- 主产物 `review_report.md` 由 `build` 生成，逐条列出 finding 的定位、原文、依据与建议，
  便于他人回到原文复核；不写整篇评语。

## 4. 合法性与一致性

- `result.json` 必须是合法 JSON（UTF-8，无 BOM）；`products.review_report` 指向的文件必须存在且非空；
- 同一输入重复运行，`findings`/`counts`/`coverage` 结构一致，只有时间戳、elapsed 等易变量进入 `runtime`；
- 任何子命令异常都退出码 0，并保留最小合法 result.json（空目录→`insufficient_evidence`，坏 JSON→`input_error`）。
