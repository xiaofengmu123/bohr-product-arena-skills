# 判定口径（references/criteria.md）

本文档定义逐条判断的统一口径，供 Agent 在步骤 4 逐条判断时使用。若 `case.json` 自带纳排标准，**以输入标准为最高优先级**，本文档只在其缺省时补位。

## 1. 三级相关性

| 标签 | 判定条件（逐条可复核） |
|-|-|
| `direct` | 研究对象（人群/物种/样本）、干预或方法、结局指标**同时**命中研究问题与纳入条件；且研究设计可支撑结论（非纯背景综述、非仅标题关键词命中） |
| `indirect` | 仅命中部分条件：主题相关但人群/方法/结局不同；或为综述、方法学、背景资料，可作参照但非直接证据 |
| `irrelevant` | 与科研问题无关，或仅标题含关键词而研究对象/设计完全不匹配 |
| `unknown` | 材料缺失（无摘要/无全文可读）无法判断，不得猜测 |

判断依据只来自标题、摘要、关键词与材料正文；**标题命中但设计不匹配者判 indirect 或 irrelevant，不得因词面命中判 direct**。

## 2. 三档 tier

| tier | 含义 | 进入条件 |
|-|-|-|
| `retain` | 直接相关，建议精读 | relevance=direct，且关键证据可溯源 |
| `pending` | 证据不足或冲突待人工 | relevance=unknown；或摘要不足无法定层；或存在未裁定的冲突 |
| `exclude` | 排除或已并入他条 | relevance=irrelevant；或为重复/版本变体已并入 canonical |

硬约束：`exclude` 只放 irrelevant 或已裁定的重复变体，不得放 indirect/direct 的活文献（保证召回）。

## 3. priority_score（0–1）

- 在 relevance/tier 之上给"用户真正会先看"的优先级，非词面命中分数。
- 基准：`direct` 0.70–1.00，`indirect` 0.30–0.69，`irrelevant` 0.00–0.29。
- 同层内部再按：证据强度（原文直接支持 > 综述转述）、样本/设计匹配度、年份新近、是否唯一关键证据微调。
- `scaffold.json` 的 `lexical.base_score` 仅为词面先验，**禁止**直接当作最终 priority_score。

## 4. 置信度

| 值 | 条件 |
|-|-|
| `high` | 摘要+全文（或丰富摘要）明确支持判断，无冲突 |
| `medium` | 仅摘要支持，或个别字段缺失 |
| `low` | 仅标题可判断，或材料不足，或存在未决冲突 |

## 5. 排序与平局

- 主键：`tier`（retain→pending→exclude）；次键：`priority_score desc`。
- 固定平局键（保证同输入同输出）：
  `priority_score desc, evidence_completeness desc, year desc, norm_title asc, record_id asc`
  其中 `evidence_completeness` = 每条 result 的 `evidence` 条目数。
- `rank` 为最终顺序 1..N；同分并列时用平局键定序，不随机。

## 6. 去重与版本冲突

| 情形 | 规则 |
|-|-|
| 规范化标题或 DOI 相同 | 自动归入同一 `dedupe_groups`，标记同源 |
| 预印本 vs 正式发表 | 组内设 `canonical` 为正式版；预印本条 `dedupe.role=preprint`、tier=exclude，理由写明"已并入 canonical" |
| 同一研究多篇报道 | 保留最完整/最直接的一篇为 retain，其余标 `dedupe` 并说明 |
| 标题高度相似（≥0.9）但 DOI 不同 | `scaffold` 标 `near_duplicate_of`，Agent 逐条裁定，不确定则 pending |

## 7. 证据要求

- 每条 `evidence[i]` 必须含 `locator`（来源定位）+ 逐字 `quote`（材料原文）。
- locator 建议格式：`case.json#papers[0].abstract`、`materials/x.pdf#p3`、`task.md#L<行号>`。
- `quote` 必须是材料中的**逐字片段**；`check` 会做子串溯源校验，编造引文会被记为 error。
- 每个关键结论（relevance 判定、tier 裁定、冲突裁定）都至少一条证据。

## 8. 诚实与降级

- 缺摘要/年份/DOI：对应字段 `null`，`relevance=unknown`、`tier=pending`、`confidence=low`，写入 `uncertainty`。
- 空候选/空输入：`status=insufficient_evidence` + `omissions`，不生成示例文献。
- 材料冲突：两处来源不一致时都记录，`conflict` 标注 `source_conflict`，不确定则升级 `pending_human`。
- 任何结论、数值、DOI、引用必须来自材料；无据写 `not_reported` 或 `pending_human`，不编造。
