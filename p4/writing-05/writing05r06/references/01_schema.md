# 结构化中间产物与输出契约（schema）

本 Skill 的核验链：`plan.json → claim_units.json → evidence_map.json → validation_report.json → result.json`。
所有中间产物落在 `/app/work/`，最终合并为 `/app/output/result.json`。字段以实际写入为准；本文件给出规范口径。

## 1. plan.json（执行计划）
```json
{
  "case_id": "string",
  "has_output_contract": true,
  "contract_keys": ["deliverable", "sources", "change_log"],
  "task_excerpt": "task.md 前 2000 字符",
  "materials": [],
  "gaps": [],
  "created_at": "ISO8601"
}
```

## 2. claim_units.json（论断清单）
每条论断是一个"可判定的最小论断单元"（一个主语 + 一个可核验的断言）。
```json
{
  "claims": [
    {
      "claim_id": "C001",
      "claim_text": "原文/底稿中的论断句",
      "claim_type": "fact|statistic|causal|mechanism|comparison|recommendation|definition|scope",
      "uncertainty_flags": ["correlation_as_causation"],
      "status": "supported|unsupported|pending_human|dropped",
      "final_wording": "按证据强度调整后的成稿措辞",
      "source_claim_id": null,
      "hedge_note": "如需人工确认，写理由"
    }
  ]
}
```

claim_type 说明：
- `fact` 事实陈述；`statistic` 数值/统计；`causal` 因果主张；`mechanism` 机制解释；
- `comparison` 组间/前后比较；`recommendation` 推荐/结论；`definition` 定义；`scope` 适用边界。

## 3. evidence_map.json（证据表，多对多）
每条 evidence 可支撑多条 claim；每条 claim 可由多条 evidence 支撑。
```json
{
  "evidence_units": [
    {
      "evidence_id": "E001",
      "claim_ids": ["C001", "C002"],
      "source_doc": "paper.txt",
      "locator": "S2.P3.s1 | P3 | line12 | 字符区间",
      "quote": "逐字摘录，必须与原文完全一致",
      "support_verdict": "supports|partially_supports|weakens|contradicts|no_support",
      "evidence_grade": "A|B|C|D|U",
      "grade_reason": "分级理由（研究设计/样本量/重复性/共识）"
    }
  ]
}
```

locator 统一格式（可回查）：
- 分节稿：`S{section}.P{para}.s{sentence}`
- 无节稿：`P{para}` 或 `line{n}`
- 表格/图：`Table{n}.row{m}` / `Fig{n}`
- 无法定位时用字符区间，如 `S2 chars 120-180`

## 4. validation_report.json（脚本校验结果）
```json
{
  "quote_checks": [
    {"evidence_id": "E001", "source_doc": "paper.txt", "locator": "P1",
     "status": "matched|matched_raw|not_found|quote_too_short", "matched": true,
     "quote": "…"}
  ],
  "issues": [
    {"type": "quote_not_found|source_not_loaded|orphan_claim_ref|bad_grade|bad_verdict|claim_missing_evidence|hedge_too_strong|hedge_too_weak",
     "evidence_id": "…", "claim_id": "…", "detail": "…"}
  ],
  "issue_count": 0,
  "checked_at": "ISO8601"
}
```

## 5. result.json（最终契约，必产）
顶层必须包含：
- `case_id`：来自 case.json（`case_id` > `caseId` > `id`）
- `status`：`complete` | `partial` | `failed` | `insufficient_evidence`
- `deliverable`：`{claims_file, evidence_file, draft, review_report, claim_count, evidence_count}`
- `sources`：逐条 `{key, value, kind, source_doc, locator, quote, support_verdict, evidence_grade, grade_reason, claim_ids}`
- `uncertainty`、`pending_human`、`change_log`
- 建议：`limitations`、`errors`、`degradations`、`runtime`

题目 `output_contract` 的每个键必须原样回显（缺失键补默认三件套 deliverable/sources/change_log，不改名、不删未知键）。
