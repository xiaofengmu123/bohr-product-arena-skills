# 理由码字典（Reason Codes）

每个理由码必须落在一条可判定的纳排标准上；凡无法判定的情形一律归入 `uncertain_*` 或
`pending_human`，不得用 `include`/`exclude` 冒充确定结论。

## 纳入（include）

| 码 | 含义 | 判据落点 |
|---|---|---|
| `in_population` | 研究对象符合纳入人群 | 标题/摘要明确研究人群 |
| `in_intervention` | 干预/暴露符合 | 标题/摘要明确干预或暴露 |
| `in_outcome` | 结局指标符合 | 标题/摘要明确报告结局 |
| `in_design` | 研究设计符合 | 标题/摘要明确设计类型 |
| `in_language` | 语种符合 | 元数据语种字段 |

## 排除（exclude）

| 码 | 含义 | 判据落点 |
|---|---|---|
| `ex_population` | 人群不符 | 标题/摘要人群描述 |
| `ex_intervention` | 干预/暴露不符 | 标题/摘要干预描述 |
| `ex_outcome` | 结局不符 | 标题/摘要结局描述 |
| `ex_design` | 设计不符 | 标题/摘要设计类型 |
| `ex_language` | 语种不符 | 元数据语种字段 |
| `ex_unrelated` | 与问题无关（须补具体判据，禁止裸写） | 标题/摘要具体片段 |
| `ex_duplicate` | 与已纳入记录为同一研究（去重漏网） | DOI/标题 |
| `ex_not_primary` | 非原始研究（评论/社论/协议等） | 标题/摘要类型 |
| `ex_no_abstract` | 摘要缺失且标题不足以判定 | not_reported |

## 边界 / 待定（uncertain）

| 码 | 含义 | 处置 |
|---|---|---|
| `uncertain_population` | 人群部分重叠 | needs_human=true，仲裁后定 |
| `uncertain_language` | 语种字段缺失或矛盾 | needs_human=true |
| `uncertain_abstract` | 仅摘要、关键信息缺失 | needs_human=true |
| `pending` | 尚未判定（骨架默认） | needs_human=true |

## 元信息（meta）

| 码 | 含义 |
|---|---|
| `not_reported` | 字段缺失，如实记录，不推断 |
| `source_conflict` | 两个来源信息不一致，两者都记录 |

## 冲突分类（conflict.type）

| 值 | 含义 |
|---|---|
| `decision` | 双人纳入/排除结论不一致 |
| `reason_code` | 结论一致但理由码不同 |
| `evidence` | 引用的证据片段不同 |

仲裁结论写入 `arbitration.decision` 与 `arbitration.rationale`；无法仲裁时保留
`uncertain` 并 `needs_human=true`，禁止用多数票掩盖实质分歧。
