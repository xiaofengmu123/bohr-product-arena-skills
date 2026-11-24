# 严重度、类型与置信度分级

本文档把"报什么级别"变成可复核的规则。每条 finding 的 `reasoning` 必须显式回答
**三问**，并据此定 `severity`，不得一律标"中"。

## 1. 严重度（severity）三问

| 级别 | 判定（三问：影响主结论？可修正？影响可复现？） |
|---|---|
| `critical` | 直接影响主结论成立，且无法仅靠改措辞修正（需补实验/补数据） |
| `major` | 影响某个重要子结论，或结论强度明显超出证据；通常需降级表述或补证据 |
| `minor` | 不影响结论方向，但表述不严谨、口径不一致、引用不精确 |
| `suggestion` | 可改可不改的改进建议，非缺陷 |

`reasoning` 模板（三问逐项作答）：
`影响主结论：是/否；可修正：改措辞/需补证据/需重做分析；影响可复现：是/否`

## 2. 问题类型（type）

| 类型 | 含义 |
|---|---|
| `overclaim` | 结论强度超出数据/设计能支撑的范围 |
| `unsupported_citation` | 引文不支持被引的那句话 |
| `method_mismatch` | 方法回答不了研究问题 |
| `statistical_misreport` | 统计口径与结论方向相反或误报 |
| `reasoning_gap` | 推理跳跃 / 隐含假设未说明 / 替代解释未排除 |
| `causality_conflation` | 相关误为因果 |
| `consistency_break` | 章节前后不一致 / 承诺未交付 |
| `norm_issue` | 学术规范问题 |
| `suggestion` | 建议性意见 |

## 3. 判定状态（status）

| 状态 | 含义 |
|---|---|
| `confirmed` | 原句即可坐实，无需外部材料 |
| `probable` | 多个证据指向，但缺少一个关键核对 |
| `pending_review` | 需外部材料（如引文全文）才能定 |
| `ruled_out` | 查证后排除（保留记录，供复核） |

## 4. 置信度（confidence）

- `high`：原文逐字可坐实，无歧义。
- `medium`：判断依赖跨段推理，但指向明确。
- `low`：只有间接证据，或依赖对领域常识的推断。

## 5. 主张判定（claim.verdict）

- `supported`：本文数据/引文足以支撑该强度表述。
- `overclaimed`：表述强度超出支撑证据。
- `unsupported`：找不到支撑证据。
- `pending`：证据不完整，待定。
