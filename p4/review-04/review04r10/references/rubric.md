# 类型、严重度与状态分级（rubric）

严重度不得一律"中"。每条 finding 的 `reasoning` 必须写清**三问**：
(1) 是否影响主结论；(2) 是否可修正；(3) 是否影响可复现性。三问的答案决定严重度。

## 一、finding 类型（type）

| type | 含义 | 示例 |
|---|---|---|
| overclaim | 结论强度超出证据 | "首次/最优/显著"但无对照或无不确定度 |
| unsupported_claim | 主张无任何支撑证据 | 断言无数据、无引用、无推导 |
| citation_mismatch | 引用文献不支持所引句 | 被引摘要与引用句方向相反 |
| methodological_gap | 方法无法回答问题 | 缺对照、无盲法却下因果结论 |
| statistical_misreport | 统计口径与结论不符 | p 值方向、效应量/CI 缺失、口径不一致 |
| causal_overreach | 相关误为因果 | 横断面/观察设计用因果动词 |
| commitment_unmet | 引言承诺未在结果/讨论交付 | 承诺分析 A，结果未提 A |
| internal_inconsistency | 前后口径/数值矛盾 | 摘要与正文样本量不一致 |
| figure_text_mismatch | 图表与正文描述不符 | 正文数值与图中数值不一致 |
| reporting_omission | 应报告而未报告 | 缺效应量、缺样本量、缺局限性 |
| normative_issue | 学术规范问题 | 缺利益冲突、缺数据可用性 |
| suggestion | 建议性意见（非缺陷） | 可读性、结构、补充分析建议 |

## 二、严重度（severity）与三问对应

| severity | 三问典型回答 | 何时使用 |
|---|---|---|
| critical | 影响主结论 × 不可忽略 × 影响可复现 | 主结论被证伪、核心数据缺失或方向相反 |
| major | 影响主结论 / 可修正 / 部分影响可复现 | 主结论强度需下调、关键证据缺失 |
| moderate | 不推翻主结论 / 可修正 / 影响可复现或可信度 | 局部方法、统计口径、跨节不一致 |
| minor | 不影响主结论 / 易修正 / 基本不影响可复现 | 表述过度、次要规范问题 |
| suggestion | 非缺陷 | 建议性意见，单独归类 |

判定口诀：先问"不修会不会导致主结论站不住"，再问"改起来是一句话还是一个实验"，最后问"别人复现时会不会卡住"。

## 三、主张判定（claim verdict）

- `supported`：原文证据足以支撑该强度的主张。
- `overclaimed`：证据存在但不足以支撑当前强度。
- `unsupported`：找不到对应证据。
- `pending`：材料不足或需外部核对，暂不下结论。

## 四、finding 状态与置信度

- `status`：`confirmed`（原文可证）/ `probable`（证据较强但未完全坐实）/ `pending_review`（需外部核对）/ `ruled_out`（核查后排除）。
- `confidence`：`high`（有逐字原文与明确判据）/ `medium`（有原文但判据需推断）/ `low`（推断成分大）。
- 任何 `pending_review` 条目必须同时写入 `result.pending_review`；外部无法核验的引用只写"需外部核对"，不得断言"引用不支持"。
