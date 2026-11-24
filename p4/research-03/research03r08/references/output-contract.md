# 输出契约与数据模板

本文件给出 `result.json` 的字段定义，以及工作区（`/app/work`）中间产物的
JSON/JSONL 模板。模板可被他人复制复用；所有 `quote` 必须逐字取自材料原文。

## 1. result.json 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| case_id | string | 是 | 取 case.json 的 case_id/caseId/id/armiId/challenge_id，缺失填 `unknown` |
| review_question | string | 是 | 固定研究问题，一句可检索的陈述 |
| subquestions | array | 是 | 子问题 ≥1，写清依赖顺序 |
| search_strategy | object | 是 | 含 `query_blocks`（见下） |
| inclusion_criteria | array | 是 | 条款化纳入标准，先于筛选落盘 |
| exclusion_criteria | array | 是 | 条款化排除标准，含优先级与冲突处理 |
| screening_records | array | 是 | 题录 + 全文两轮逐条筛选记录 |
| prisma_flow | object | 是 | 由脚本从 screening_records 重算的计数 |
| quality_appraisal | array | 是 | 逐研究质量评价 |
| synthesis | object | 是 | `{conclusions, conflicts, gaps}` |
| evidence_map | array | 是 | 逐字证据卡，每条含 locator+quote |
| uncertainty | object | 是 | 冲突、偏倚、空白、无法确定项 |
| status | string | 是 | ok / partial / insufficient_evidence / input_error |
| sources | array | 是 | 实际读到的材料清单 |
| change_log | array | 是 | 带时间戳的增量修改记录 |
| limitations / errors / degradations / pending_human | array | 是 | 诚实标注 |
| runtime_seconds | number | 是 | 运行耗时（脚本/Agent 记录） |

## 2. query_blocks 模板

```json
{
  "database": "PubMed",
  "query": "(term1 OR synonym1) AND (term2 OR synonym2) AND filters",
  "fields": "title/abstract",
  "intent": "命中核心人群与干预",
  "date_range": "2015-01-01:2025-12-31",
  "language": ["en"],
  "notes": "受控词/同义词说明",
  "status": "run | not_run"
}
```

`status=not_run` 表示数据库不可用或超时，`hits` 不得伪造。

## 3. screening_records 模板（screening.jsonl，每行一条）

```json
{
  "record_id": "S001",
  "source": "作者. 标题. 期刊, 年",
  "stage": "title_abstract | full_text",
  "decision": "include | exclude | unclear | not_retrieved",
  "reason": "排除理由（decision=exclude 时必填）",
  "criterion_id": "I3 | E2（回指纳排条款编号）",
  "judge": "agent（双人筛选分歧时写 agent_a/agent_b）",
  "duplicate_of": "S000（重复文献指向主记录）",
  "locator": "材料内定位",
  "note": "备注"
}
```

## 4. quality_appraisal 模板（quality_appraisal.jsonl）

通用六维量表（可按领域选择或替换，但每维必须有依据定位）：

1. selection_bias（选择偏倚）
2. confounding（混杂控制）
3. measurement（暴露/结局测量）
4. attrition（失访/完整性）
5. reporting（报告完整性）
6. applicability（对研究问题的适用性）

```json
{
  "record_id": "S002",
  "tool": "custom-6dim",
  "dimensions": [
    {"name": "confounding", "rating": "low|some|high|unclear",
     "basis_locator": "paper.md#p3", "quote": "adjusted for age and sex"}
  ],
  "overall_grade": "high | moderate | low | critical",
  "impact_on_conclusions": "该等级如何影响后续综合（如排除或降权）"
}
```

## 5. evidence_cards 模板（evidence_cards.jsonl）

```json
{
  "evidence_id": "EV001",
  "source": "S002",
  "claim": "一句话论断",
  "field": "population | intervention | outcome | effect",
  "locator": "paper.md#p5",
  "quote": "逐字原文片段",
  "certainty": "reported | suggestive | not_reported"
}
```

## 6. synthesis.json 模板

```json
{
  "conclusions": [
    {"id": "C1", "text": "结论文本", "evidence_ids": ["EV001"], "certainty": "supported"}
  ],
  "conflicts": [
    {"id": "X1", "text": "冲突描述", "source_a": "S002", "source_b": "S005",
     "resolution": "undetermined | resolved_by"}
  ],
  "gaps": [
    {"id": "G1", "text": "空白描述", "scope": "具体缺口范围", "why_unfilled": "原因"}
  ],
  "uncertainty": {"conflicts": [], "bias": [], "gaps": [], "unknowns": []}
}
```

## 7. 类型约定（缺值回填）

- 标量字段缺值填 `null` 或 `""`（按 `output_contract` 提示的类型）。
- 数组字段缺值填 `[]`；对象字段缺值填 `{}`。
- 未知键原样保留，不改名、不删除。
