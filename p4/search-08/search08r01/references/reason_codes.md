# 理由码字典（reason codes）

每个纳排判定必须带一个理由码。理由码本身是审计字段，进入 `evidence_map` 的
`field="reason_code"` 项；其**逐字依据片段**写入 `evidence_quote` 并指向标题/摘要。

## 纳入（inc_*）

| 码 | 含义 | 依据片段应指向 |
|---|---|---|
| `inc_pico_match` | 研究问题、人群、干预、结局、设计均与 PICO 相符 | 摘要中的人群/干预/结局描述 |
| `inc_population` | 研究对象人群符合纳入标准 | 摘要中的研究对象描述 |
| `inc_intervention` | 干预/暴露符合纳入标准 | 摘要中的干预描述 |
| `inc_outcome` | 结局指标符合纳入标准 | 摘要中的结局描述 |
| `inc_study_design` | 研究设计符合纳入标准（如 RCT、队列、系统综述） | 摘要中的设计描述 |
| `inc_background` | 作为背景/方法学参考文献纳入 | 标题或摘要的领域归属 |

## 排除（ex_*）

| 码 | 含义 | 依据片段应指向 |
|---|---|---|
| `ex_not_relevant` | 与研究问题无关（**禁止裸写**，须附具体判据） | 摘要主题偏离处 |
| `ex_wrong_population` | 研究对象人群不符 | 摘要人群描述 |
| `ex_wrong_intervention` | 干预/暴露不符 | 摘要干预描述 |
| `ex_wrong_outcome` | 结局不符 | 摘要结局描述 |
| `ex_wrong_design` | 研究设计不符（如仅评论/编辑按语） | 摘要设计描述 |
| `ex_not_original_research` | 非原始研究（社论、评论、新闻） | 标题/摘要体裁标志 |
| `ex_wrong_language` | 语种不符纳入标准 | 摘要/题录语种 |
| `ex_duplicate` | 与已纳入记录为同一研究（去重层漏网） | 双方标题/DOI |
| `ex_retracted` | 已撤稿 | 题录撤稿标记 |
| `ex_abstract_only_unresolvable` | 仅摘要且信息不足以判定，且无法获取全文 | 摘要缺失处 |

## 待定（un_*，一律 `needs_human=true`）

| 码 | 含义 | 处置 |
|---|---|---|
| `un_abstract_insufficient` | 摘要信息不足以判定 | 转人工，建议读全文 |
| `un_language_unclear` | 语种无法从题录判断 | 转人工确认语种 |
| `un_population_partial` | 研究对象部分重叠、边界不清 | 转人工按标准裁量 |
| `un_fulltext_needed` | 需全文才能判定的关键信息缺失 | 转人工获取全文 |

## 冲突码（conflict.type）

- `decision`：两人纳/排结论相反。
- `reason_code`：结论相同但理由码不同。
- `evidence`：引用的证据片段不同或彼此矛盾。

仲裁字段 `arbitration` 必须写明依据；僵持不下写 `uncertain` 并置 `needs_human=true`，
不得用平均或多数掩盖实质争议。
