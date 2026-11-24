# 理由码字典（reason_codes）

每条记录必须给出 `decision` + `reason_code`；非 `uncertain`/`pending_human` 的判定还须给出
`evidence_quote`（标题/摘要中的逐字片段）。脚本 `finalize` 会校验证据子串可命中，并把无法
命中的写入 `degradations`（不掩盖、不静默）。

## 纳入（include）

| 码 | 含义 | 何时使用 |
|---|---|---|
| `include_relevant` | 明确相关 | 标题+摘要明确落在研究问题与纳入标准内 |
| `include_borderline` | 边界纳入 | 摘要信息不足但倾向相关，建议下一步读全文确认 |

## 排除（exclude）

| 码 | 含义 | 何时使用 |
|---|---|---|
| `ex_not_relevant` | 不相关 | 主题与研究问题无关（需给出具体判据，不许只写"不相关"） |
| `ex_wrong_population` | 对象不符 | 人群/物种/样本与纳入标准不符 |
| `ex_wrong_intervention` | 干预/暴露不符 | 处理、暴露或自变量不符 |
| `ex_wrong_outcome` | 结局不符 | 结局指标与研究问题无关 |
| `ex_wrong_design` | 设计不符 | 研究设计类型被标准排除（如仅病例报告） |
| `ex_language` | 语言不符 | 语言超出检索协议的语种限制 |
| `ex_duplicate_merged` | 去重合并 | 同一研究的副本，已并入代表记录 |
| `ex_retracted` | 已撤稿 | 记录为撤稿文献 |
| `ex_no_abstract` | 无摘要可判 | 无标题摘要信息，无法据协议判定，须标 `needs_human` |
| `ex_out_of_scope` | 超出范围 | 出版类型/时间/来源等超出协议范围 |

## 不确定 / 待人工（uncertain / pending）

| 码 | 含义 | 何时使用 |
|---|---|---|
| `uncertain` | 无法确定 | 证据不足以判定，且不足以给出倾向 |
| `pending_human` | 待人工确认 | 需研究者最终拍板，模型输出只能作为建议 |
| `not_reported` | 未报告 | 关键元数据（DOI/摘要/年份）缺失 |
| `source_conflict` | 来源冲突 | 不同来源对同一记录信息不一致 |

## 使用规则

1. `decision` 与 `reason_code` 必须一致：`include*` 对应 `include`，`ex_*` 对应 `exclude`，
   `uncertain`/`pending_human` 对应 `uncertain` 或 `pending_human`。
2. `evidence_quote` 必须是记录 `title` 或 `abstract` 中可逐字命中的片段；写
   `not_reported` 表示该字段确实缺失（会被识别为显式缺失，不算 mismatch）。
3. 排除理由不许用空泛的"不相关"：`ex_not_relevant` 必须在 `evidence_quote` 中指出
   记录主题与纳入标准冲突的具体片段，或在 `arbitration`/`notes` 中写明判据。
4. 两位标注者意见不一致时写 `conflict` 对象并仲裁到 `arbitration`；僵持条目保留两条
   意见并标 `needs_human=true`，不得用多数票静默掩盖分歧。
