# result.json 输出契约与字段规格

`/app/output/result.json` 是本 Skill 的主产物，必须始终可被 `json.load` 解析，且无论运行到哪一步都包含全部信封字段（失败时用最小合法骨架兜底）。

## 1. 顶层结构

```json
{
  "case_id": "原样保留 case.json 的 case_id",
  "…output_contract 全部字段原样回填…": null,
  "status": "ok|partial|insufficient_evidence|input_error",
  "review_report": "review_report.md",
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "errors": [],
  "degradations": [],
  "changes": [],
  "change_log": [],
  "runtime": {},
  "result": {
    "findings": [],
    "unchecked_items": [],
    "verdict_summary": {}
  }
}
```

## 2. 关键规则

1. `case_id` 原样保留 `case.json` 的 `case_id`（可回退 `id`/`task_id`），不得丢失或改写。
2. `output_contract` 的全部字段**原样回填**：不改名、不删除；脚本按 `contract_object()` 生成，未知字段值写 `null`（脚本内无法确定的类型统一 `null`）。运行时 Agent 需把真正的主结果映射回这些字段（例如合同字段 `findings` 应指向审查清单）。
3. 追加信封字段（由脚本保证存在）：`status`、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`changes`、`change_log`、`runtime`。
4. 主结构 `result.findings[]`（逐条问题清单）+ `result.unchecked_items[]`（未检查/不适用项）+ `result.verdict_summary`（总体结论与覆盖统计）。

## 3. status 取值

| 值 | 含义 |
|---|---|
| `ok` | 六类相关分面已查并有结果 |
| `partial` | 部分完成，`unchecked_items`/`pending_human` 写明缺口 |
| `insufficient_evidence` | 材料不可用/为空，无法开展审查 |
| `input_error` | `case.json` 坏 JSON、不可读等输入错误 |

诚实原则：材料不足写 `insufficient_evidence`，缺软件/缺来源写 `degradations` + `limitations`，绝不编造数据、引用、DOI 或结论。

## 4. finding 字段（单条问题）

| 字段 | 必填 | 取值/说明 |
|---|---|---|
| `finding_id` | 是 | 稳定编号 `F01`、`F02`…（顺序稳定，便于比对） |
| `facet` | 是 | `fact_check`/`citation_support`/`method_design`/`statistics_report`/`argument_chain`/`academic_norms` |
| `severity` | 是 | `critical`/`major`/`minor`/`suggestion` |
| `issue_type` | 是 | `factual_error`/`insufficient_evidence`/`standards_issue`/`suggestion`/`source_conflict` |
| `verdict` | 是 | `confirmed`/`to_review`/`not_checked` |
| `claim` | 是 | 本文论断/问题描述（一句话） |
| `location` | 是 | `para7` 或 `p3#para2`（与 `show` 输出的 locator 一致） |
| `quote` | 是 | 逐字原文（可跨行不跨段），已由 `check` 逐字命中 |
| `rationale` | 是 | 判断依据（为什么是这个问题、依据哪条判据） |
| `revision_suggestion` | 是 | 可执行修改建议（给编辑/作者可直接交办的动作） |
| `confidence` | 是 | `high`/`medium`/`low` |
| `evidence` | 是 | 数组，每项 `{"source_id","locator","quote"}`；引用支持类必须含被引文献主张与本文论断的对应 |

`evidence[]` 的 `source_id` 与 `sources[]` 中的 `source_id` 一一对应（`D1`、`D2`…），`locator` 采用与 `show` 相同的段落定位。

## 5. unchecked_items 字段

```json
{"facet": "citation_support", "reason": "无法核验", "detail": "参考文献缺失，未臆断被引文献主张"}
```

六类 facet 必须全部被处理：有 finding，或在此处说明不适用/待复核原因。

## 6. sources 字段

```json
{"source_id": "D1", "path": "materials/paper.pdf", "kind": "pdf",
 "chars": 12345, "problems": ["..."]}
```

用于回到原文核对：`path` 相对输入目录，`kind` 为 `text/pdf/docx/image/other`。

## 7. verdict_summary 字段

```json
{
  "overall_verdict": "pass|revision_required|to_review|insufficient_evidence|input_error",
  "counts": {"critical": 0, "major": 1, "minor": 0, "suggestion": 0, "total": 1},
  "coverage": {"fact_check": "checked", "citation_support": "declared_unchecked", "…": "…"}
}
```

## 8. 信封字段语义

- `uncertainty`：数值/来源冲突、歧义、待确认项（每个都带 locator）。
- `pending_human`：需人工复核的事项（OCR 失败、扫描件、无法核验的引用）。
- `errors`：解析/读写失败记录，每项 `{"file","error"}`。
- `degradations`：降级处置记录（超大文件跳过、PDF 无文本层、缺软件等）。
- `changes`：本次相对骨架的增量改动摘要。
- `change_log`：带时间戳的步骤记录。
- `runtime`：运行时间/步骤/命令等易变量（仅此处记录易变信息，保证主结构可复现）。
