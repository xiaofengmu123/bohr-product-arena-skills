# 判定标准与规则（criteria.md）

本文件是 relevance / tier / priority_score / 去重 / 冲突 / 证据的统一口径。
若 `case.json` 自带纳排标准或 `output_contract`，**以输入标准为最高优先级**，
本文件仅在输入未提供时作为默认口径。任何结论必须来自输入材料，
无据写 `unknown` / `pending_human` / `not_reported`，不得编造。

## 1. 相关性三级（relevance）

| 值 | 判定条件 | 典型边界 |
|-|-|-|
| `direct` | 研究对象、方法、人群、结局、时间范围**至少一项与科研问题直接匹配**，且摘要提供了可核对证据 | 原始研究（RCT/队列等）的 PICO 与问题一致；方法论文直接解决该问题 |
| `indirect` | 部分匹配：背景资料、方法参考、相邻人群、综述、或仅标题命中但设计/人群不匹配 | 综述讨论同一主题但非原始证据；方法参考支撑但研究对象不同 |
| `irrelevant` | 与科研问题无实质关联，仅为噪声 | 标题含关键词但主题/对象完全不同 |
| `unknown` | 摘要/年份/DOI 缺失或材料不足以判断 | 只回退为 `unknown`，不猜测 |

判定顺序：先看能否排除（irrelevant），再看能否确认（direct），
中间地带一律 `indirect`；信息不足一律 `unknown`。

## 2. 三档筛选层（tier）

| 值 | 含义 | 与 relevance 的对应 |
|-|-|-|
| `retain` | 用户应优先阅读；进入排序前部 | 通常 `direct`；证据充分 |
| `pending` | 待人工复核：摘要不足、冲突未决、或 relevance 为 `indirect`/`unknown` 但无法直接排除 | `indirect` / `unknown` / 存在冲突 |
| `exclude` | 明确不相关，从候选集中剔除 | 仅 `irrelevant` |

**铁律**：`exclude` 只放 `irrelevant`；`indirect`/`unknown` 不得进入 `exclude`
（保召回）。`tier` 与 `relevance` 不一致时必须在 `reasons` 说明。

## 3. 优先级打分（priority_score，0–1）

- 0.80–1.00：`direct` 且证据完整（研究设计/人群/结局清晰），关键文献
- 0.55–0.79：`direct` 但证据不完整，或高质量 `indirect`（方法/综述直接支撑）
- 0.30–0.54：一般 `indirect` / 背景资料
- 0.00–0.29：`irrelevant`（进入 `exclude`）
- 分数是**语义判断后的排序先验**，不是词面信号；`scaffold.json` 的
  `base_score` 仅是词面先验，**不得**直接当最终分数。
- 同一 tier 内同分时使用固定平局键（见下）。

## 4. 排序与平局（确定性）

最终排序按 `tier` 区段（retain → pending → exclude），区段内按：

```
priority_score desc, evidence_completeness desc, year desc,
norm_title asc, record_id asc
```

`evidence_completeness` 取 `scaffold.json` 中该记录的确定性数值；
`norm_title` 为小写去非字母数字后的标题。相同输入两次运行结果一致。

## 5. 去重与版本变体

- 依据归一化标题（小写去标点）或归一化 DOI 聚类（见 `scripts/core_impl.py`）。
- 每组裁定一个 `canonical_record_id`：正式发表（有 venue）优先于预印本；
  同型时年份新者优先；再按 DOI 有无；最后 `record_id` 升序。
- 每条结果 `dedupe` 字段取值：
  `canonical` / `duplicate_of:<record_id>` / `version_variant` / `null`。
- 预印本与正式版、同一研究多篇报道：保留 canonical，其余标注 `duplicate_of`，
  在 `dedupe_groups` 记录裁定与理由；存在冲突证据时升级 `pending_human`。

## 6. 冲突处理

- 标注分歧：同一条目出现两个来源的 relevance/元数据不一致 → `conflict`
  记录双方 + 裁决理由；无法裁决 → `tier=pending` 并写 `pending_human`。
- 摘要不足：`relevance=unknown`、`tier=pending`、`confidence=low`，写 `uncertainty`。
- 元数据冲突（年份/DOI/作者）：全部记录进 `conflict`，不静默选一。
- 同分大量：用平局键稳定；若仍并列，在 `reasons` 注明并列及键值。

## 7. 证据要求（可复核的硬约束）

- 每条 `relevance` / `tier` / `priority_score` 的关键决定必须有 `evidence`，
  每项含 `locator`（材料定位，如 `case.json#papers[2].abstract` 或
  `materials/xxx.pdf#p3`）+ 逐字 `quote`（原文片段，非转述）。
- `quote` 必须是材料中真实存在的原文；无原文可引时置空并在 `uncertainty`
  说明，不得改写原文冒充引用。
- 缺失年份/DOI/摘要：字段填 `null`，不猜测；在 `uncertainty` 记录。
