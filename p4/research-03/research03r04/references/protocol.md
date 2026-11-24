# 综述协议、量表与工作文件 Schema

本文件是 SKILL.md 的可复核细则：检索式构造、纳排条款、质量评价量表、证据/不确定性模型，以及 `/app/work/` 下各中间文件的字段约定。Agent 按此 Schema 落盘，`finalize`/`selfcheck` 按同一 Schema 组装与校验，保证“换人复跑”可得出基本一致的纳入集与结论。

## 1. 研究问题拆解（protocol.json 顶层）

- `review_question`：单一、可回答、含 PICO 或等价要素（对象/干预或暴露/对照/结局/场景）的研究问题。
- `subquestions`：数组，每项 `{id, text, depends_on[]}`；`depends_on` 写清子问题之间的先后/依赖关系，使检索覆盖可被审计。

## 2. 检索式（search_strategy）

`search_strategy` 至少含 `search_date`、`databases[]`、`query_blocks[]`。每个 query_block：

```json
{"id": "QB1", "database": "PubMed", "fields": ["tiab"],
 "query": '("Treatment X"[tiab]) AND (mortality[tiab] OR death[tiab]) AND humans[MeSH]',
 "intent": "解释这一段检索式想命中什么：人群 + 干预/暴露 + 结局",
 "date_range": "2015-01-01:2026-11-22", "language": "English"}
```

判定要点：`query` 必须含布尔逻辑（AND/OR/NOT）与字段限定；`intent` 必须非空；数据库不可用时该块标 `not_run` 并记入 `degradations`，不得伪造 `hits`。

## 3. 纳排标准（先于筛选落盘）

- `inclusion_criteria[]` / `exclusion_criteria[]`，每项 `{id, text, priority, applies_to}`；`text` 必须是**可判定条款**（研究类型、对象、时间、语言、可得全文等），不是“质量较高”这类空话。
- 每条含 `priority` 与冲突处理规则：条款冲突时按优先级高的裁决；同优先级无法裁决则并列记录、标 `undetermined`，不得在筛选途中悄悄放宽。

## 4. 两轮筛选记录（screening.jsonl，每行一个 JSON）

```json
{"record_id": "R1", "round": "title_abstract", "decision": "include",
 "reason": "title/abstract 报告了目标结局", "criterion_id": "I1",
 "judge": "ai", "confidence": "high", "duplicate_of": null}
```

- `round` ∈ `title_abstract | fulltext`；`decision` ∈ `include | exclude | pending | duplicate`。
- 每条 `exclude` 必须同时给 `reason` 与 `criterion_id`；`duplicate` 必须给 `duplicate_of`。
- `finalize` 由记录重算 `prisma_flow`（identified → title_abstract → fulltext → included 与各轮排除原因计数），不做手工计数。

## 5. 质量评价量表（quality_appraisal.jsonl）

采用可解释的 CASP 风格清单（可随学科替换，但维度必须显式）：

| 维度 | 判定 | 必须写 |
|---|---|---|
| internal_validity | low / unclear / high | basis_locator + 逐字 quote |
| reporting | low / unclear / high | basis_locator + 逐字 quote |
| external_validity / applicability | low / unclear / high | basis_locator + 逐字 quote |
| risk_of_bias | low / unclear / high | basis_locator + 逐字 quote |

每行：

```json
{"record_id": "R1", "tool": "casph-like", "overall_grade": "moderate",
 "dimensions": [{"dimension": "internal_validity", "judgement": "low",
                 "basis_locator": "materials/paper1.txt#L2", "quote": "…逐字…"}],
 "effect_on_conclusion": "downgrade to suggestive strength"}
```

`overall_grade` ∈ `high | moderate | low | critical`；`effect_on_conclusion` 必须说明等级如何改变结论强度，不能只写“质量较高”。

## 6. 证据卡（evidence_cards.jsonl）与结论强度

证据卡每行：

```json
{"evidence_id": "EV1", "record_id": "R1", "claim": "…",
 "locator": "materials/paper1.txt#L2", "quote": "…逐字…",
 "confidence": "high", "type": "reported"}
```

- `confidence` ∈ `high | moderate | low | insufficient`：由全文可得性、测量精度与一致性共同决定；仅摘要 → 至多 `low`。
- `type` ∈ `measured | reported | inferred | not_reported`：区分“材料直接测量/报告的数值”与“推断”，推断必须标 `inferred` 并降强度。
- `quote` 必须逐字取自材料；`selfcheck` 空白归一后做子串核验，未命中报 `no_verbatim_match`。

结论强度阶梯（synthesis.json `conclusions[]`）：

| strength | 触发条件（示例） |
|---|---|
| established | 多条独立全文研究、方向一致、质量可接受 |
| suggestive | 单一研究，或仅摘要/部分全文 |
| preliminary | 仅探索性/未全文核验的证据 |
| conflicting | 证据互相矛盾且无法裁定 |
| not_reported | 材料未报告该结局 |

每条结论必含 `{id, text, evidence_ids[], strength, scope}`；`evidence_ids` 必须命中 `evidence_map`，`scope` 写清适用对象/条件，不越界。

## 7. 冲突、空白与不确定性（synthesis.json）

- `conflicts[]`：`{id, description, source_a, source_b, evidence_ids[], resolution}`；`source_a/source_b` 必须是两个不同来源；`resolution` 为 `undetermined` 时不强取一方。
- `gaps[]`：`{id, kind, description, impact}`；`kind` 建议用 `population | method | date | geography | evidence | reporting`，`impact` 说明缺口如何限制结论。
- `uncertainty[]`：`{type, item, effect, handling}`；`type` 至少覆盖 `source_conflict / missing_fulltext / abstract_only / extraction_failed / single_study / date_gap / not_reported / language_limit`。
- `limitations[]` / `pending_human[]`：可读字符串，描述缺数据/缺软件/需人工裁定项；**不编造**。

## 8. 确定性与可复跑

- 所有中间文件固定字段名与字段顺序；`finalize` 用 `json.dump(..., sort_keys=True, ensure_ascii=False, indent=2)` 确定性写入。
- 证据 id 使用稳定命名（如 `EV1`、`R1`、`C1`），同一输入两次运行结果逐字节一致（`runtime_seconds` 除外）。
- `run_state.json` 记录 `case_id/contract/inventory/started_at/phase`，支持任务中断后从当前阶段续跑。
