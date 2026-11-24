# result.json 字段规格

`build` 由 `draft.json` + `index.json` 确定性生成最终 `/app/output/result.json`。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | 任意 | 原样保留 `case.json` 的 case_id（或 id/task_id），不得丢失 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `output_contract` | 任意 | `case.json` 的 output_contract 逐字节回填（无则缺省） |
| *契约键* | 任意 | output_contract 的每个键在顶层回填；draft 未提供时写 `null`（键名含 list/items 或以 s 结尾写 `[]`） |
| `result` | object | 审查主结构（见下） |
| `sources` | list | 材料清单，每项含 source_id/path/kind/chars/paras/pages/text_file/skipped/problems |
| `uncertainty` | list | 不确定项、`source_conflict` 等 |
| `pending_human` | list | 待人工复核项 |
| `errors` | list | 输入解析/读取错误 |
| `degradations` | list | 降级处置记录（超长跳过、无文本层等） |
| `changes` | list | 结构化变更记录（step/field/action） |
| `change_log` | string | 可读变更摘要；确定性优先，不写墙钟时间 |
| `runtime` | object | **唯一易变区**：started/finished 时间戳、耗时、python 版本、输入目录、材料数 |

`status` 语义：
- `ok`：六类相关 facet 已查（有 finding 或列入 `verified_facets` 表示查过且无问题），主结构完整；
- `partial`：部分完成，缺口见 `unchecked_items`/`pending_human`；
- `insufficient_evidence`：材料不可用/缺失；
- `input_error`：case.json 坏 JSON 等输入错误。

## result 子结构

```json
{
  "findings": [...],
  "unchecked_items": [{"facet": "...", "reason": "..."}],
  "verdict_summary": {
    "overall_verdict": "pass|issues|to_review|incomplete",
    "counts": {
      "findings_total": 0,
      "by_facet": {"fact_check": 0, "...": 0},
      "by_severity": {"critical": 0, "...": 0},
      "by_verdict": {"confirmed": 0, "...": 0}
    },
    "coverage": {
      "facets_with_findings": [],
      "facets_checked_clean": [],
      "facets_not_checked": []
    }
  }
}
```

- `verified_facets`（draft 中）表示“查过且未发现问题”的 facet，避免被误判为漏查。
- `overall_verdict`：confirmed 的 critical/major → `issues`；有 finding 否则 `to_review`；无 finding 且 status=ok → `pass`；status 为 insufficient_evidence/input_error → `incomplete`。

## 单条 finding

```json
{
  "finding_id": "F01",
  "facet": "fact_check|citation_support|method_design|statistics_report|argument_chain|academic_norms",
  "severity": "critical|major|minor|suggestion",
  "issue_type": "factual_error|insufficient_evidence|standards_issue|suggestion",
  "verdict": "confirmed|to_review|not_checked",
  "claim": "本文论断/问题描述",
  "location": "para7 或 p3#para2",
  "quote": "逐字原文（可跨行不跨段）",
  "rationale": "判断依据",
  "revision_suggestion": "可执行修改建议",
  "confidence": "high|medium|low",
  "evidence": [{"source_id": "D1", "locator": "...", "quote": "..."}]
}
```

- `quote` 必须是原文逐字片段；`check` 用 `normalize_ws` 折叠空白后逐段匹配，报 `quote not found verbatim` 就回原文重抄。
- `location` 与 `quote` 的段落必须对应；`evidence` 是支撑判定的材料内证据，source_id 须存在于 `sources`。

## 确定性契约

同一输入两次运行，剥离 `runtime` 后 `result.json` 必须字节一致。`selftest.py` 的第 5 项检查即验证这一点。Agent 在 draft 中**不得**写入时间戳、随机数或依赖运行序的字段。
