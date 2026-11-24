# 判定标准与证据要求（references/criteria.md）

本文件是逐条判断的唯一口径来源；case.json 自带纳排标准时以输入标准为最高优先级。
脚本只提供词面先验与校验，**relevance / tier / priority_score 只能由 Agent 依据本文件与材料给出**。

## 1. 相关性四级

| 标签 | 判定条件 | 示例边界 |
|-|-|-|
| `direct` | 研究问题所要求的研究对象/人群、方法/干预、结局指标、时间范围**同时命中**（至少命中核心条件），可作直接证据 | RCT、正确人群、正确结局 |
| `indirect` | 部分命中：同领域但人群/方法/结局不同；或方法学参考、背景综述、证据链中间环节 | 系统综述、体外/动物先证、相邻人群 |
| `irrelevant` | 主题/人群/结局不匹配；或明确被排除标准命中（如动物研究、错误疾病） | 错误疾病、错误人群、无关结局 |
| `unknown` | 摘要缺失或信息不足，无法判断；**不猜测**，进 pending | 无摘要、仅标题 |

判定顺序：先查排除标准（命中即 `irrelevant`）→ 再查纳入标准（全命中=`direct`，部分=`indirect`）→ 信息不足=`unknown`。

## 2. 分层 tier

- `retain`：`direct` 与 `indirect`（所有相关文献都保留，保召回）。
- `pending`：`unknown`、冲突未裁定、摘要不足需人工；**尽量小**，每项写清待人工点。
- `exclude`：仅 `irrelevant`。**exclude 不得放入 direct/indirect**。

## 3. priority_score（0–1，只对 retain/pending 有意义）

| 区间 | 含义 |
|-|-|
| 0.75–1.00 | direct，核心证据，方法与结局均匹配 |
| 0.40–0.74 | indirect，方法参考/背景/部分匹配 |
| 0.10–0.39 | unknown/待定，信息不足 |
| 0.00 | irrelevant/exclude |

同分用确定性平局键（见 SKILL.md 第 4 节）打破，禁止随机。

## 4. 纳排条件匹配

- 每条结果必须写 `criteria_matched[]`（命中的纳入条件）与 `criteria_ids[]`（对应排除条件命中时注明）。
- 标题含关键词但人群/设计不匹配者，**不得**判 direct（防词面过拟合）。

## 5. 去重与冲突裁定

| 情形 | 规则 |
|-|-|
| 预印本 vs 正式发表 | 正式版为 primary（retain），预印本 `dedupe="duplicate_of:<正式版id>"`；归入 `dedupe_groups` |
| 同一研究多篇报道 | 保留最完整/最原始一篇，其余 `dedupe="secondary_of:<主id>"` |
| 摘要不足 | `relevance=unknown`、`tier=pending`、`confidence=low`，写 `uncertainty` |
| 摘要与全文矛盾 | 双方证据都记录，`conflict` 说明，升级 `pending` |
| 标注标准互相冲突 | 记录冲突，写 `pending_human`，不擅自选一方 |

## 6. 证据溯源（本版强化，硬要求）

每条理由（claim）必须可追溯：

- `reasons[i]` 为对象：`{reason_id, text, criteria_ids[], evidence_ids[]}`。
- `evidence[i]` 为对象：`{evidence_id, locator, field, quote, claim_refs[], verified}`。
- `locator` 精确到字段，如 `case.json#papers[0].abstract` 或 `materials/<file>#p3`。
- `quote` 为材料**逐字原文**（finalize 会计算 `quote_hash` 并做 verbatim 校验）。
- 一条 reason 可引用多条 evidence；一条 evidence 可支撑多条 reason（claim_refs）。
- **不得**引用不存在的字段、不虚构 DOI/数值/引文；缺失写 `not_reported` 或进 `pending_human`。

## 7. decision_path（可解释排序）

每条结果建议写 `decision_path[]`：命中规则 → 中间结论 → 最终标签，例如：

```json
[{"rule": "exclusion:animal", "result": "irrelevant"},
 {"rule": "tier_map", "result": "exclude"}]
```

## 8. 置信度

`high`：摘要完整且与标准明确一致/明确不一致；
`medium`：有摘要但存在边界判断；`low`：摘要缺失或需全文才能确定。
