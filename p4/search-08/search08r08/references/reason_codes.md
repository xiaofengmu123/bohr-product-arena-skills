# 理由码字典（回溯重判用）

每条纳入/排除/待定判定必须给出 `reason_code`，且 `include`/`exclude` 必须附逐字
`evidence_quote`（可在标题或摘要中命中）。理由码一旦定版即随 `criteria_version` 冻结；
标准修订时升级 `criteria_version`，由 `pipeline.py criteria-diff` 检出需回溯重判的记录。

## 纳入（in_*）

| 码 | 含义 | 使用条件 |
|-|-|-|
| `in_full` | 完全符合 | 标题/摘要同时满足全部纳入标准、不触发任何排除标准 |
| `in_scope` | 主题相关 | 满足研究问题范围，但摘要信息不足以确认全部 PICOS 要素 |
| `in_partial` | 部分重叠 | 研究对象/干预部分重叠，需全文确认后方可最终纳入 |

## 排除（ex_*）

| 码 | 含义 | 使用条件（须附逐字片段） |
|-|-|-|
| `ex_population` | 人群不符 | 研究对象人群与纳入标准不符 |
| `ex_intervention` | 干预/暴露不符 | 干预、暴露或方法不符合 |
| `ex_outcome` | 结局不符 | 报告的结局指标不在范围内 |
| `ex_study_design` | 设计不符 | 研究设计类型不在纳入范围（如系统综述仅纳入 RCT） |
| `ex_language` | 语种不符 | 非纳入语种 |
| `ex_time_range` | 时间窗口外 | 发表时间超出检索时间范围 |
| `ex_publication_type` | 文献类型不符 | 评论、社论、会议摘要、勘误等非原始研究 |
| `ex_not_relevant` | 不相关 | 主题无关；**必须写明具体判据**，禁止只写“不相关” |
| `ex_duplicate` | 重复记录 | 与另一记录为同一研究副本（去重阶段已合并，保留 trace） |
| `ex_fulltext_unavailable` | 全文不可得 | 仅摘要且无法获取全文，不足以做最终判断 |

## 待定（unc_*）——模型不能确定的，一律标待定，不编造

| 码 | 含义 | 使用条件 |
|-|-|-|
| `unc_fulltext_needed` | 需全文精读 | 标题/摘要不足以判定，需要全文 |
| `unc_scope_overlap` | 范围部分重叠 | 人群/干预/结局部分重叠，无法凭摘要裁定 |
| `unc_abstract_only` | 仅摘要 | 仅有摘要且缺全文，关键要素无法确认 |
| `unc_missing_meta` | 元数据缺失 | DOI/年份/语种等关键元数据缺失 |
| `unc_language_ambiguous` | 语种存疑 | 语种信息缺失或混用 |

## 冲突与仲裁

`conflict` 记录分歧点，必须给出 `type`（`decision` / `reason_code` / `evidence` 之一），
仲裁结论写入 `arbitration`；两位标注者僵持时 `needs_human=true`，绝不静默取一人意见。

| 码 | 含义 |
|-|-|
| `conflict_decision` | 纳入/排除结论分歧 |
| `conflict_reason` | 结论一致但理由码不同 |
| `conflict_evidence` | 结论一致但所引证据片段不同 |

## 状态码（status）

`ok | partial | insufficient_evidence | input_error`。材料不足必须如实写
`insufficient_evidence`，部分完成写 `partial`，不编造数据冒充 `ok`。
