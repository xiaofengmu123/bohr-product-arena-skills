# 判定口径（criteria）

本文定义相关性、分层、打分、去重与证据的**可复核口径**。若 `case.json` 自带纳排标准或
`output_contract`，以输入标准为最高优先级，本文只在输入缺失时兜底。

## 1. 相关性三级（relevance）

| 标签 | 判定条件（全部从材料推断，不靠标题猜测） |
|-|-|
| `direct` | 研究对象、人群、方法、结局、时间范围与科研问题及纳入条件实质匹配；能作为该问题的直接证据。 |
| `indirect` | 仅部分匹配：方法相关但人群/结局不同、背景或方法学参考、间接支持同一机制的文献。 |
| `irrelevant` | 主题关键词相似但对象/设计/结局均不匹配；或明显超出范围（物种、人群、领域、时间）。 |
| `unknown` | 摘要/年份/全文不足，无法判定。必须同时给 `tier=pending`、`confidence=low/unknown`，写入 `uncertainty`。 |

判断顺序：先读摘要/全文事实 → 匹配纳入条件 → 匹配排除条件 → 再给标签。标题出现关键词但
对象/设计不符，**不得**判 `direct`（评分考察点 A3）。

## 2. 分层三档（tier）

| tier | 含义 | 对应 relevance |
|-|-|-|
| `retain` | 保留，排在用户会看的位置 | direct（允许个别 indirect 作为方法/背景核心参考，但需在 reasons 说明为什么必须保留） |
| `pending` | 待人工复核：证据冲突、摘要不足、版本归属未定、间接相关但需判断 | indirect / unknown / 有 conflict |
| `exclude` | 排除，仅放 irrelevant | irrelevant |

规则：`exclude` 只放 `irrelevant`；`retain` 不放 `irrelevant`/`unknown`。违反时 `merge` 记 warning，
由 Agent 修正或写入 `pending_human`。

## 3. 打分口径（priority_score，0–1，可解释）

最终分数由脚本在 `merge` 阶段按**固定公式**由标签与证据推导，不采纳 Agent 手写任意分：

```
priority_score = min(base + evidence_bonus + recency_bonus, 1.0)
base           = direct:0.70 / indirect:0.40 / irrelevant:0.05 / unknown:0.10
evidence_bonus = 0.15（evidence 至少一条 locator+quote 均非空），否则 0
recency_bonus  = 0.10（year≥2018）/ 0.04（有年份且<2018）/ 0（无年份或 irrelevant）
```

`score_components` 三项随每条结果写出，供复核。**词面先验 `lexical_overlap`（scaffold）不进入**
最终分，仅作 Agent 参考；标题词面命中不等于相关性。

## 4. 排序与平局（确定性）

先按 `tier`（retain→pending→exclude），层内再按固定平局键：

```
priority_score desc → evidence 条数 desc → year desc → norm_title asc → record_id asc
```

同输入两次运行结果一致；`rank` 为 1..N 重排后的顺序。`k` 值：若输入 `output_contract` 带
`k`/`top_k` 则回填并按序取前 k；缺省 `k=null`（不截断，全部条目保序输出，保证召回完整）。

## 5. 去重与冲突

- **同 DOI（归一化后相等）**：判为同一作品重复导出，自动归 `dedupe_groups`（kind=`same_doi`）。
- **标题归一化后相同**：疑为预印本/正式版或同一研究多报道，归 `dedupe_groups`（kind=`title_match`），
  版本归属由证据确认，未确认前 `pending_human`。
- **同一第一作者同年、标题与 DOI 均不同**：弱信号，不自动合并，记入 `conflicts`
  （type=`possible_same_study`，status=`pending_human`），升级人工。
- 冲突不覆盖源记录：primary 保留，成员标 `dedupe.role=duplicate` 并指回 `canonical_record_id`。

## 6. 证据要求（溯源）

- 每条 `retain/pending`（relevance≠unknown）必须至少一条 `evidence`，含 `locator`（定位到
  材料路径/字段/段落，如 `materials/<file>.md#p3`、`case.json#papers[0].abstract`）与逐字 `quote`。
- `quote` 必须逐字来自材料，不得改写；PDF 须实际 `pdftotext` 抽取后再引用，不得按文件名/领域标签补造。
- 缺证据时该条相关性与排序降级并写入 `uncertainty`，不得编造。
