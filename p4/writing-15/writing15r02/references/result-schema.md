# result.json 字段说明与填写指南

本文件是 `/app/output/result.json` 的契约说明，供 Agent 在 Phase 4 前逐字段核对。字段值以实际材料为准；缺值用 `null` / `[]`，不得编造。

## 顶层结构

```json
{
  "case_id": "string（来自 case.json 的 id/case_id/challenge_id）",
  "status": "ok | partial | insufficient_evidence | input_error",
  "output_contract": { "...": null },
  "artifacts": { "名称": "相对 /app/output 的路径" },
  "target_deliverable": "主产物说明或路径",
  "evidence_map": [ { "claim": "...", "locator": "...", "quote": "..." } ],
  "version_and_limits": { "diff": [], "limitations": [], "pending_human": [] },
  "sources": [ { "locator": "...", "quote": "...", "claim": "..." } ],
  "uncertainty": [ { "point": "...", "reason": "...", "status": "pending_human|not_reported|source_conflict" } ],
  "change_log": [ { "before": "...", "after": "...", "why": "..." } ],
  "errors": [], "degradations": [], "pending_human": [],
  "runtime": { "scaffold_started_at": "...", "finalized_at": "...", "elapsed_seconds": null }
}
```

## 字段规则

- `output_contract`：若 `/input/case.json` 含 `output_contract` / `outputContract` / `output_contract_fields`，**原样回填全部键**（缺值 `null`/`[]`，不改名、不删未知键）。若题目未给机器可读契约，使用默认三键：`target_deliverable`、`evidence_map`、`version_and_limits`。
- `status` 诚实分级：
  - `ok`：主产物完成、可核验、无 unresolved 关键项。
  - `partial`：已产出可用部分，但存在缺口或无法判定项。
  - `insufficient_evidence`：材料不足，无法开始或只能给空骨架。
  - `input_error`：输入无法解析（坏 JSON、缺 case.json 等）。
- `sources` / `evidence_map`：每项必须含 locator（如 `materials/paper.md#p3` 或文件路径+段落锚）+ **逐字 quote**（材料原文片段）+ claim（对应论断）。quote 必须逐字，不允许转述后冒充原文。
- `uncertainty.status` 取值：`pending_human`（需人工确认）、`not_reported`（材料未报告）、`source_conflict`（多来源不一致）。
- `change_log`：每条改动给出 before（原句）/ after（改后句）/ why（原因）。局部修改必须保留原句。
- `errors`：读取/解析/校验错误；`degradations`：降级处理（超长跳过、编码降级、无预览等）；`pending_human`：需要作者确认的事项清单。
- `runtime`：时间戳与耗时；模型请求次数由平台记录，本 Skill 不自报评测分数或请求计数。

## 主产物（artifacts）

- 文本：`.md`/`.json`/`.csv`，UTF-8。
- 图形：可编辑 SVG + 真实 PDF（PDF 以 `%PDF-` 开头）。
- Office/幻灯片/图表：`.docx`/`.pptx`/`.xlsx` + 真实渲染 PDF/PNG 预览。
- 每个 artifact 键值用相对 `/app/output` 的路径；finalize 会校验路径存在，缺失项写入 errors。

## 填写顺序建议

1. scaffold 先落骨架（status=partial），保证 3 分钟内已有合法 result.json。
2. Phase 1 分批写 `work/updates/evidence_partial.json`（可多个，Agent 自行合并）。
3. Phase 4 前把最终字段合并成 `work/updates/result_update.json`，由 `finalize` 深合并进骨架并校验。
4. 永远不要为了“看起来完成”把 status 写成 ok：有缺口就 partial，材料不足就 insufficient_evidence。
