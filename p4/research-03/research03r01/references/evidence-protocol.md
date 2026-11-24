# 证据溯源协议（evidence-protocol）

本文件定义本 Skill 的**证据优先**数据模型与可复用模板。目标：每条结论都能
指回材料中的逐字片段，且换人复跑能得出基本一致的纳入集与结论。

## 1. 证据行（`/app/work/evidence.jsonl` 每行）

```json
{
  "evidence_id": "E001",
  "source_id": "S01",
  "locator": {"file": "materials/paper-a.txt", "page": 3, "anchor": "Results, 2nd paragraph"},
  "quote": "逐字引用的原文片段",
  "claim": "归一化后的一句话主张",
  "claim_type": "fact",
  "pico": {"element": "outcome", "concept": "主要结局指标"},
  "confidence": "high",
  "extraction_status": "verbatim",
  "note": ""
}
```

### 字段约束

| 字段 | 取值 | 说明 |
|---|---|---|
| `evidence_id` | `E` + 3 位数字，全局唯一 | 由 Agent 分配；`finalize` 按此排序 |
| `source_id` | `S` + 2 位数字 | 必须命中 `sources.jsonl` 注册表 |
| `locator` | dict 或 `"file::page::anchor"` | `file` 为相对 `/input` 的路径；脚本按 basename 回退查找 |
| `quote` | string，非空 | **逐字**取自材料；仅允许空白归一化 |
| `claim_type` | `fact \| estimate \| inference \| not_reported` | 强制区分材料事实 / 合理推断 / 无法确定 |
| `confidence` | `high \| moderate \| low` | quote 未逐字核对的证据不得标 `high` |
| `extraction_status` | `verbatim \| paraphrase_flagged \| abstract_only \| not_verified \| extraction_failed` | 诚实标注提取状态 |

### 逐字核对规则（脚本自动执行）

1. `finalize`/`selfcheck` 重开 `locator.file` 对应文件，读取文本并做空白归一化。
2. 归一化后的 `quote` 是否为文件文本的子串？是 → `quote_check.verified=true, status=verbatim`。
3. 否 → 依原因标 `quote_mismatch / no_text_layer / file_unavailable / no_locator / empty_quote`，并把 `extraction_status` 由 `verbatim` 降为 `not_verified`、`confidence` 由 `high` 降为 `low`。
4. **不允许把“对不上”的证据静默当作逐字证据**；宁可标注，不可假装。

## 2. 结论行（`/app/work/synthesis.json` 的 `conclusions`）

```json
{
  "conclusion_id": "C1",
  "statement": "纳入证据支持……",
  "evidence_ids": ["E001", "E002"],
  "support_level": "supported",
  "boundary": "仅限本材料范围内的结论，不越界",
  "basis": [
    {"evidence_id": "E001", "role": "direct_support", "locator": {"file": "materials/paper-a.txt"}, "quote": "原文片段"}
  ]
}
```

- `support_level ∈ {supported, partially_supported, undetermined, contested}`。
- `basis` 内联 `locator + quote`，使 `review.md` 直接呈现证据，不必再查表。
- `evidence_ids` 中的每个 id 都必须能在 `evidence_map.evidence` 中找到（`selfcheck` 校验）。

## 3. 冲突矩阵（`synthesis.conflicts`）

```json
{"conflict_id": "X1", "type": "contradiction", "evidence_ids": ["E003", "E004"],
 "sources": ["S01", "S03"], "note": "两项研究对同一结局方向相反", "resolution": "undetermined"}
```

- 每条 ≥2 个 `evidence_ids`；类型建议 `contradiction / divergence / terminology`。
- 无法裁定就并列记录并标 `undetermined`，不强行取一方。

## 4. 可复用模板

### 4.1 检索式模板（每块）

```json
{"id": "Q1", "database": "PubMed", "fields": "ti,ab",
 "query": "(\"term A\" OR \"synonym A2\") AND (\"population B\" OR MeSH:B) AND (\"outcome C\")",
 "intent": "命中核心文献，说明为何如此组合",
 "time_limit": "2015-01-01 至 2025-12-31", "language_limit": "en,zh", "run_status": "run", "hits": 0}
```

`query` 必须可执行（布尔逻辑 + 同义词/受控词），禁止退化为一个关键词串；`hits` 只在真实检索后填写。

### 4.2 纳排标准模板

```json
{"id": "I1", "text": "纳入随机对照试验或前瞻性队列研究", "applies_to": "study_design", "priority": 1}
{"id": "E1", "text": "排除无全文或仅会议摘要的记录", "applies_to": "availability", "priority": 2}
```

条款先于筛选落盘；冲突处理规则建议写为“排除条款优先于纳入条款”。

### 4.3 质量评价量表模板（示例维度，可按学科调整）

| 维度 | 评分 | 依据 |
|---|---|---|
| 研究设计 | high/moderate/low | basis_locator + quote |
| 偏倚风险 | high/moderate/low | basis_locator + quote |
| 结果报告完整性 | high/moderate/low | basis_locator + quote |
| 外部效度 | high/moderate/low | basis_locator + quote |

`overall` 综合维度给出；`impact_on_conclusions` 说明该等级如何影响后续综合。

### 4.4 筛选记录模板

```json
{"source_id": "S01", "round": "title_abstract", "decision": "include",
 "reason": "标题/摘要符合 PICO", "criterion_id": "I1", "judge": "skill-agent"}
{"source_id": "S02", "round": "fulltext", "decision": "exclude",
 "reason": "报告无主要结局数据", "criterion_id": "E2", "judge": "skill-agent"}
```

两轮都要有；排除必有 `reason + criterion_id`；去重记 `duplicate_of`。

## 5. 与自检的对应关系

- `scripts/main.py validate --kind evidence` 只查 schema；
- `scripts/main.py finalize` / `selfcheck` 做逐字核对并把结果写入 `quote_check`；
- 未通过逐字核对的证据不会被判定为“可回链”，从而在评分中如实降级而非伪造。
