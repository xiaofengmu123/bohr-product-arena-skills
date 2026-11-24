# 理由码字典（reason_codes）

每条唯一记录的判定必须落在三类决策之一，并携带**一个**理由码；非 `uncertain` 记录还必须给出能在该记录 `title`/`abstract` 中**逐字命中**的 `evidence_quote`。理由码中的 `criterion_ref` 把判定链接到协议的纳入/排除标准，保证“标准 → 判据 → 决策”可回溯。

## 1. include（纳入）

| code | criterion_ref | 定义 | evidence_required |
|---|---|---|---|
| `in_topic_match` | 纳入标准·研究问题 | 研究问题/主题直接匹配 | 标题或摘要中指向研究问题的逐字段落 |
| `in_population` | 纳入标准·人群 | 研究人群落在纳入人群范围 | 标题/摘要中的人群表述 |
| `in_intervention` | 纳入标准·干预/暴露 | 干预或暴露与纳入一致 | 干预/暴露表述 |
| `in_outcome` | 纳入标准·结局 | 报告了纳入结局指标 | 结局指标表述 |
| `in_design` | 纳入标准·研究设计 | 研究设计符合纳入类型 | 设计类型表述 |
| `in_all_criteria` | 全部纳入标准 | 逐条满足全部纳入标准 | 覆盖各判据的多个片段 |

## 2. exclude（排除）

| code | criterion_ref | 定义 | evidence_required |
|---|---|---|---|
| `ex_not_topic` | 排除标准·主题 | 与研究生问题不相关 | 说明不相关的具体片段（禁止只写“不相关”） |
| `ex_wrong_population` | 排除标准·人群 | 研究人群明显不符 | 人群表述片段 |
| `ex_wrong_intervention` | 排除标准·干预 | 干预/暴露不符 | 干预表述片段 |
| `ex_wrong_outcome` | 排除标准·结局 | 未报告所需结局 | 结局缺失/不符片段 |
| `ex_wrong_design` | 排除标准·设计 | 研究设计不符（如综述、个案） | 设计类型片段 |
| `ex_wrong_publication_type` | 排除标准·文献类型 | 信函/评论/社论/会议摘要等 | 文献类型片段 |
| `ex_out_of_scope` | 排除标准·范围 | 超出检索/纳入范围 | 范围片段 |
| `ex_duplicate_merged` | 去重阶段 | 已由去重合并（一般不进纳排表） | 去重 cluster_id |

## 3. uncertain / pending_human（待定，不编造）

这些是**边界情形**：材料不足以给出确定的纳入/排除，必须显式 `needs_human=true`，不能强行归类。

| code | 触发情形 | 处置 |
|---|---|---|
| `unc_abstract_only` | 仅有摘要、无全文可核验 | pending_human；如需可降级为“按摘要纳入候选” |
| `unc_language` | 语种限制可能排除但内容未知 | pending_human，注明语种 |
| `unc_partial_overlap` | 人群/干预/结局部分重叠 | pending_human，注明重叠点 |
| `unc_missing_metadata` | DOI/年份/期刊等关键元数据缺失 | pending_human，注明缺失项 |
| `unc_criteria_ambiguous` | 当前标准版本对边界情形未定义 | 冻结标准并记录，或回写 protocol 后重判 |
| `unc_source_conflict` | 多来源元数据冲突（如同一 DOI 两处标题不同） | 记录两来源，标 source_conflict，pending_human |
| `unc_missing_judgment` | 脚本发现唯一记录缺判定（自动占位） | pending_human |

## 4. 分歧类型（conflict.type）

| type | 定义 |
|---|---|
| `decision` | 两位标注者 decision 不同 |
| `reason_code` | decision 相同但理由码不同 |
| `evidence` | 引用的证据片段不同或一方无法命中 |

仲裁记录 `arbitration` 必须给出 `decision`、`basis`（依据）；僵持不清则 `needs_human=true`，禁止用平均/多数直接掩盖。

## 5. 降级码（degradations / limitations.type）

| code | 触发 | 写入位置 |
|---|---|---|
| `oversize` | 单文本文件 >2MB | degradations + limitations |
| `bad_json` | case.json / 记录文件 JSON 解析失败 | degradations |
| `bad_encoding` | 编码无法以 UTF-8 解析，回退后仍含替换字符 | degradations + limitations |
| `doi_missing` | 记录缺 DOI | limitations |
| `title_missing` | 记录缺标题 | limitations |
| `abstract_missing` | 记录缺摘要 | limitations |
| `evidence_mismatch` | evidence_quote 未逐字命中 | degradations |
| `missing_contract` | 未提取到 output_contract | limitations |
| `empty_input` | 输入目录为空 | limitations（status=insufficient_evidence） |
| `count_not_closed` | 各环节计数不闭合 | errors |
| `missing_judgment` | 唯一记录缺判定 | errors |

## 6. 使用规则

1. 每条记录一个 `reason_code`，不得为“纳入/排除”打码却无片段。
2. `evidence_quote` 必须逐字来自 `title` 或 `abstract`（`finalize` 自动校验，未命中写入 `degradations` 并把 `evidence_verified=false`）。
3. 标准一旦定版（`criteria_version`）即冻结；若中途改标准，必须回改此前已判记录，并在 `change_log` 记录版本变更。
4. 缺材料就用 `unc_*` + `pending_human`，**不编造** DOI、标题、数值或结论。
