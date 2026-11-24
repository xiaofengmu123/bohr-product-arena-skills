# 相关性判定与分层口径（criteria）

本文件是可复核筛选的标准口径。若 case.json 自带纳排标准，**以输入标准为最高优先级**；本口径仅在其缺失或含糊时兜底。

## 1. 相关性三级标签

| 标签 | 判定条件 | 典型触发 |
|-|-|-|
| `direct` | 研究对象、人群、方法、结局（或视角）与科研问题同域且直接回答问题；可作为核心证据或直接方法依据 | 同病种/同人群/同指标且设计可回答主问题 |
| `indirect` | 与研究问题部分相关的背景、方法参考、相邻人群/指标、综述；有参考价值但不直接回答主问题 | 系统综述、方法学论文、相近主题的初始研究 |
| `irrelevant` | 主题、对象、方法均不匹配，无法提供有效证据或方法参考 | 标题有同关键词但对象/设计不匹配 |
| `unknown` | 信息不足以判断（缺摘要且材料不可得） | 仅标题、无摘要、无全文可得 |

**硬规则**：标题出现关键词不等于相关；若摘要/全文显示研究对象、纳入人群或研究设计与科研问题不匹配，即使标题命中也应判 `irrelevant` 或 `indirect`。综述与原始研究不得混评：综述通常为 `indirect`（除非主问题就是“综述当前证据”）。

## 2. 三档 tier

| tier | 含义 | 进入条件 |
|-|-|-|
| `retain` | 用户应优先看的核心/有价值文献 | `relevance ∈ {direct, indirect}` 且确有证据价值 |
| `pending` | 待人工复核或信息不足以裁决 | `relevance=unknown`、摘要不足、冲突未决、疑似重复待裁定 |
| `exclude` | 噪声，可排除 | `relevance=irrelevant`；**exclude 只放 irrelevant** |

**边界约定**：`indirect` 文献可进 `retain`（排在 `direct` 之后）也可进 `pending`，取决于其方法/背景价值；无摘要支撑价值时进 `pending`，不得凭标题强推成 `retain`。

## 3. 优先级打分口径（0–1）

`priority_score` 是与相关性层级联合的顺序权重，只反映“该文献在 retain 组内应排多前”，须由 Agent 依据材料判断，不得直接用 `scaffold.json` 的 `base_score`。参考锚点：

- 0.90–1.00：直接相关且方法/对象/结局与主问题高度吻合的核心证据，信息完整。
- 0.70–0.89：直接相关但有局限（样本小、年代久、设计较弱），或高价值间接方法/综述。
- 0.50–0.69：间接相关、背景资料、有价值但非关键。
- 0.30–0.49：勉强相关、仅部分维度匹配。
- 0.00–0.29：基本不相关（通常 tier=exclude）。
- 未知/待定：`priority_score` 置 `null`，仅在能给出可解释估计时才填数值。

## 4. 去重与冲突裁定

- **preprint vs published**：同一研究的预印本与正式发表版视为同一研究，归入同一 `dedupe_group`，以正式发表版（或信息更完整者）为 `canonical`，另一条 `dedupe.role=duplicate_of`，在理由中注明。
- **同一研究多报道**：标题高度相似（归一化标题相同或词面 Jaccard ≥ 0.82 且版本状态不同）归入同一组；无法确认是否同一研究时记 `conflict` 并 `pending_human`，不擅自合并。
- **标注分歧/摘要不足**：两个来源对同一指标的判断不一致时，都记录并标 `source_conflict`，升级 `pending_human`。
- **大量同分**：靠确定性平局键稳定（见下），并在 `change_log` 注明同分规模。

## 5. 证据要求

- 每个关键结论（relevance/tier/priority_score 的核心理由）至少一条 `evidence`，含 `locator`（如 `case.json#papers[3].abstract`、`materials/paper.md#p2` 或 `materials/xxx.pdf#page3`）与**逐字 `quote`**。quote 必须是从材料原文截取的片段，不得改写。
- 冲突信息不隐藏：`conflict` 字段记录待裁定的分歧与两个来源。
- 不虚构 DOI、数值、引用；缺失写 `pending_human` 或 `not_reported`，在 `uncertainty` 中说明。

## 6. 确定性平局键

排序层级：`tier`（retain < pending < exclude）组内按 `priority_score desc`，再按 `evidence_completeness desc`（非空 quotes 数 + 有无 reasons），再 `year desc`（缺年排最后），再 `norm_title asc`，最后 `record_id asc`。相同输入两次运行结果完全一致，不引入随机。