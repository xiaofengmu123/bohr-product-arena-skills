# 中间表示 Schema 与确定性组装规则

本文件定义 Skill 的结构化中间产物字段。Agent 负责把实质内容写进
`/app/work/` 下的这些文件；脚本只读取它们并确定性组装 `/app/output/`，
不反向改写中间产物。所有示例均为示意，非正式案例答案。

## 1. plan.json —— 冻结的版本化协议（先于筛选落盘）

`plan.json` 是机器可读的综述协议，必须在写第一条 `screening.jsonl` 之前完成，
并置 `frozen: true`。此后若修改纳排标准，必须递增 `protocol_version` 并在
`ledger.jsonl` 追加一条 `criteria_update` 记录，否则 `selfcheck` 会告警
"criteria not marked frozen"。

```json
{
  "protocol_version": "1.0",
  "frozen": true,
  "frozen_at": "2026-01-01T00:00:00Z",
  "review_question": "……",
  "subquestions": [
    {"id": "sq1", "question": "……", "depends_on": [], "rationale": "……"}
  ],
  "search_strategy": {
    "query_blocks": [
      {
        "id": "q1", "database": "PubMed", "fields": "ti,ab",
        "query": "(A) AND (B)",
        "synonyms": ["……"], "controlled_terms": ["MeSH ……"],
        "time_range": "2020-2026", "language_limits": "English",
        "intent": "……", "status": "run", "hits": 0, "run_date": "……"
      }
    ]
  },
  "inclusion_criteria": [
    {"id": "in1", "statement": "……", "priority": 1,
     "applies_to": "title_abstract", "version": 1}
  ],
  "exclusion_criteria": [
    {"id": "ex1", "statement": "……", "priority": 1,
     "applies_to": "fulltext", "version": 1}
  ],
  "quality_rubric": {
    "dimensions": [
      {"id": "q_design", "name": "……", "definition": "……", "scoring": "……"}
    ],
    "grades": ["high", "moderate", "low", "critical"]
  }
}
```

字段要求：

- `query_blocks[].query` 必须是可执行的布尔检索式，不是单个关键词串；
  `intent` 说明这一段检索服务于哪个子问题。
- `inclusion_criteria` / `exclusion_criteria` 每条都要有 `id`、可判定的
  `statement`、数字 `priority`（数值小者优先，冲突时按 priority 裁决）和
  `applies_to`（`title_abstract` / `fulltext`）。
- `quality_rubric` 是质量评价量表本身（维度定义 + 等级），供复用；逐研究的
  打分写在 `quality.jsonl`。

## 2. screening.jsonl —— 两轮筛选决策台账（追加式）

每行一条决策记录，`round` 取值 `title_abstract` 或 `fulltext`。

```json
{"record_id": "scr-1", "round": "title_abstract", "source_id": "s1",
 "source_label": "……", "decision": "include", "reason": "",
 "criterion_id": [], "judge": "agent", "duplicate_of": null,
 "judge2": null, "disagreement": "none"}
```

规则：

- `decision` 只取 `include` / `exclude` / `maybe`。
- 每条 `exclude` 必须写 `reason`（可查理由）和 `criterion_id`（指向
  `plan.json` 中已冻结的条款 id，可多条）。`selfcheck` 会校验引用存在。
- `duplicate_of` 非空表示该记录是重复项，指向保留记录的 `record_id`。
- 双人判定分歧：`judge2` + `disagreement`（`none` / `resolved` / `unresolved`）；
  `unresolved` 会在 `selfcheck` 告警，提示仲裁后再改 `resolved`。

## 3. quality.jsonl —— 逐维度质量评价

```json
{"study_id": "s1", "dimensions": [
  {"dimension_id": "q_design", "rating": "low_risk",
   "basis_locator": "paper.md#p3", "quote": "……逐字……"}
 ], "overall_grade": "moderate",
 "impact_on_conclusion": "……该等级如何影响后续结论……"}
```

每条维度的 `basis_locator + quote` 必须取自原文；`overall_grade` 必须落在
`plan.json` 的 `quality_rubric.grades` 中；`impact_on_conclusion` 说明该等级
如何抬升或降级结论强度（`supported` / `suggestive` / `not_reported`）。

## 4. evidence.jsonl —— 证据卡

```json
{"evidence_id": "ev-1", "source_id": "s1", "locator": "paper.md#p3",
 "quote": "……逐字……", "claim_type": "measured", "confidence": "high",
 "notes": "……"}
```

- `quote` 必须逐字取自材料；`locator` 使用 `文件名#页码/段落/行` 定位。
- `claim_type` 三分类：`measured`（材料中的观测/报告）、`inferred`（合理推断）、
  `assumed`（假设）；用于防止结论越过证据范围。
- `evidence_id` 用内容哈希（`core_impl.stable_id`）或人工稳定 id 均可，但同一
  输入必须得到同一 id。

## 5. synthesis.json —— 综合结论 / 冲突 / 空白

```json
{
  "conclusions": [
    {"id": "c1", "statement": "……", "evidence_ids": ["ev-1"],
     "claim_type": "inference", "strength": "suggestive"}
  ],
  "conflicts": [
    {"id": "x1", "claim": "……", "source_a": "s1", "source_b": "s2",
     "evidence_ids": ["ev-1", "ev-2"], "resolution": "undetermined"}
  ],
  "gaps": [
    {"id": "g1", "description": "……", "location": "……",
     "evidence_basis": "ev-1"}
  ],
  "uncertainty": {"notes": "……", "bias_risks": [], "unresolved": []}
}
```

- 每条结论的 `evidence_ids` 必须命中 `evidence_map`（`selfcheck` 强制回链）。
- 每条冲突必须并列至少两个来源（`source_a` / `source_b`），无法裁定就写
  `resolution: undetermined`，不强行取一方。
- 每条空白必须落到具体缺口位置（哪篇材料缺什么），不写"研究不足"这类泛句。

## 6. ledger.jsonl —— 追加式人工/决策修改台账

```json
{"id": "led-1", "ts": "……", "action": "criteria_update",
 "before": "……", "after": "……", "reason": "……", "actor": "agent"}
```

`finalize` 把 `ledger.jsonl` 原样作为 `result.json.change_log`（无 ledger 时回退到骨架/命令日志）；任何对 `plan.json`、筛选决定、结论的实质修改都应追加一条 ledger，保持可追溯。

## 7. 确定性组装规则（finalize 做什么）

1. 读 `plan.json` 回填协议字段；读四个 `.jsonl` 回填记录。
2. `prisma_flow` 一律由 `screening.jsonl` 重算，禁止手写计数。
3. `sources` 从筛选/证据/冲突记录去重派生，禁止手写清单。
4. `status` 由完整性判定：协议 + 筛选 + 质量 + 证据 + 结论齐全为 `ok`，
   否则 `partial`；无材料为 `insufficient_evidence`；case.json 不可解析为
   `input_error`。
5. `change_log` 取 `ledger.jsonl`，缺省回退到骨架/命令日志。
6. `selfcheck` 覆盖：合同字段齐全、exclude 有 reason+criterion_id、
   PRISMA 与重算一致、证据卡有 locator+quote、结论 evidence_ids 命中证据卡、
   冲突并列双来源、空白有具体落位。
