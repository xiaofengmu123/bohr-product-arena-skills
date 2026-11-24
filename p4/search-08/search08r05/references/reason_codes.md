# 理由码字典（判定表 reason_code 取值）

本字典覆盖纳排三态与冲突/仲裁，是回溯重判与第三人复核的共用词汇。Agent 只允许使用下表中
存在的码；确实没有合适码时写 `uncertain` + `pending_human`，不得自创语义模糊的码。

## 纳入（decision=include）

| 码 | 含义 | 判定要点 |
|---|---|---|
| `in_scope` | 研究问题/对象在范围内 | 标题或摘要明示研究对象与 PICO/研究问题对齐 |
| `in_population` | 人群/样本符合纳入标准 | 摘要含受试者/物种/样本描述 |
| `in_intervention` | 干预/暴露符合纳入标准 | 摘要含处理、暴露、自变量描述 |
| `in_outcome` | 结局/因变量符合纳入标准 | 摘要含结局指标或效应方向 |
| `in_design` | 研究设计符合纳入标准 | 摘要含 RCT/队列/病例对照/横断面等设计词 |
| `in_language` | 语种符合且摘要可用 | 可判定的标题/摘要存在 |

## 排除（decision=exclude）

| 码 | 含义 | 判定要点 |
|---|---|---|
| `ex_population` | 对象不符合 | 人群/物种/样本明显不符 |
| `ex_design` | 研究设计不符 | 综述/评论/会议摘要/方案/社论等非目标设计 |
| `ex_outcome` | 结局不符 | 摘要未涉及目标结局 |
| `ex_duplicate` | 同一研究副本 | 已在去重环节合并，或该条为冗余来源 |
| `ex_language` | 语种不符 | 标题/摘要非目标语种且无译 |
| `ex_abstract_only` | 仅摘要且信息不足 | 无全文可判定，信息不足以纳入 |
| `ex_not_relevant` | 主题不相关 | 必须有证据片段支撑，禁止裸标“不相关” |
| `ex_retracted` | 撤稿/勘误 | 材料给出撤稿或勘误标注 |
| `ex_no_abstract` | 无标题且无摘要 | 无法做标题摘要筛选 |

## 待定（decision=uncertain）

| 码 | 含义 | 处置 |
|---|---|---|
| `uncertain_overlap` | 对象部分重叠 | 需全文确认纳入边界，标 `needs_human=true` |
| `uncertain_abstract` | 摘要信息不足 | 写 `not_reported`，标 `needs_human=true` |
| `uncertain_metadata` | 元数据缺失/冲突 | 如 DOI 缺失、年份不一致，标 `needs_human=true` |
| `uncertain_reviewer` | 标注者僵持 | 冲突未仲裁，标 `needs_human=true` |
| `uncertain_unfilled` | 尚未完成双人判定 | 模板未填即汇总，转人工，标 `needs_human=true` |

## 冲突类型（conflict.type）

| 码 | 含义 |
|---|---|
| `decision_conflict` | 两位标注者对 include/exclude 判断相反 |
| `reason_conflict` | 决定一致但理由码不同 |
| `evidence_conflict` | 引用的证据片段不同或不一致 |

## 仲裁结论（arbitration.outcome）

| 码 | 含义 |
|---|---|
| `arb_include` | 仲裁为纳入 |
| `arb_exclude` | 仲裁为排除 |
| `arb_uncertain` | 仲裁后仍不确定，转人工 |
| `arb_no_conflict` | 复核后判定无实质冲突 |

## 证据溯源约定

- `evidence_quote` 必须是该记录标题或摘要中的**逐字片段**（`finalize` 会做子串校验）。
- `evidence_verified` 由 `finalize` 写入：`true` 命中、`false` 未命中（记入 degradations）、
  `null` 表示该条不适用（如 `uncertain` 无引用）。
- 同一指标两来源不一致时，两边都保留并标 `source_conflict`，不得静默取一。
