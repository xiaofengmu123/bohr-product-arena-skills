# 理由码字典与判据映射（references/reason_codes.md）

本字典供 Agent 在 `judgments.json` 中填写 `decision` 与 `reason_code` 时使用；每条非
`uncertain` 判定必须同时给出 `evidence_quote`（标题/摘要中的逐字片段）。

## 决策值

| decision | 含义 |
|---|---|
| `include` | 符合当前版本全部纳入标准，进入候选集 |
| `exclude` | 命中至少一条排除标准（需给出具体 `ex_*` 码 + 证据） |
| `uncertain` | 材料不足以判定，转 `pending_human`，禁止猜 |

## 纳入理由码

| 理由码 | 含义 | 使用条件 |
|---|---|---|
| `in_scope` | 全部符合纳入标准 | 研究对象、干预、结局、设计均匹配 |
| `in_partial_overlap` | 研究对象部分重叠但仍在范围内 | 记录中只有一部分人群/干预落入研究问题 |

## 排除理由码（禁止裸“不相关”）

| 理由码 | 含义 | 需要指向的证据 |
|---|---|---|
| `ex_population` | 研究对象不符 | 标题/摘要中的人群表述 |
| `ex_intervention` | 干预/暴露不符 | 标题/摘要中的干预表述 |
| `ex_outcome` | 结局不符 | 标题/摘要中的结局表述 |
| `ex_design` | 研究设计不符 | 标题/摘要中的设计表述（如纳入限 RCT） |
| `ex_publication_type` | 出版物类型不符 | 综述、社论、会议摘要、试验方案等 |
| `ex_language` | 语种不符 | 标题/摘要语种表述 |
| `ex_no_abstract` | 仅标题、无摘要或摘要空 | 记录缺摘要字段（结合 `uncertain` 判定） |
| `ex_irrelevant` | 明显不相关 | 仍须附上标题/摘要片段，不允许无证据裸排除 |

## 不确定 / 待人工码

| 理由码 | 含义 |
|---|---|
| `uncertain_abstract_only` | 仅摘要、信息不足以定 |
| `uncertain_conflict` | 两位标注者僵持，无法仲裁 |
| `uncertain_missing_meta` | DOI/年份/作者等元数据缺失且影响判断 |
| `uncertain_language_boundary` | 语言边界（如仅英文摘要、原文为其他语种） |

## 分歧类型（conflict.type）

| type | 含义 |
|---|---|
| `decision` | 一人 include、一人 exclude |
| `reason_code` | 同一 decision、不同理由码 |
| `evidence` | 引用的证据片段不一致 |

## 判据落地要求

1. 每条纳入/排除标准都要改写为**可判定判据**（“对象是 X / 干预是 Y / 结局是 Z / 设计是 W”），
   写入 `plan.json` 的 `criterion_judgeability`，不允许只写口号式标准。
2. 判据定版后冻结 `criteria_version`；中途修改标准必须升级版本号并对已筛记录回改，否则
   `finalize` 会报 `criteria_drift` 并写入 `degradations`。
3. 证据片段必须能在该记录的标题或摘要中逐字命中；脚本 `finalize` 自动校验，未命中写入
   `uncertainty.evidence_mismatches`，`selfcheck` 报 FAIL。
4. 全文（PDF）证据可用，但 `evidence_locator` 须含 `pdf`/`fulltext`/`body` 字样；脚本对此
   不自动校验，`evidence_verified` 记为 null，由研究者复核。
