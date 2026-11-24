# 六类检查清单与防误判守则

本清单是 Agent 逐段审查时的操作依据。每条 finding 必须能回到原文某一句话（locator + 逐字 quote）。
`quote` 会由 `scripts/main.py update` 自动回验语料：若在 `work/corpus.md` 中核不到，会被标
`quote_match=false` 并进 `pending_review`，切勿编造原文。

## 0. 主张抽取（先做，全维度的输入）

- 从摘要、引言、结果、讨论抽取**作者的核心主张及其限定条件**。
- 每条主张登记为 `claims[]`，字段：`id / text（逐字原文）/ speaker / section / locator / verdict / evidence`。
- `speaker` 只能取三者之一：
  - `author`：本文作者自己提出的主张；
  - `cited`：转述他人/所引文献的观点（如 "Smith et al. showed..."）；
  - `assumption`：作者明确标注的假设情形（如 "assuming...", "hypothetically"）。
- 引言里"本文将要分析/验证 X"类句子登记为 `commitments[]`（`promise` + `locator`），后面与结果/讨论做承诺—交付对照。
- 不要把 `cited` 的观点当作作者主张来评判，也不要把 `assumption` 当作作者声称的事实。

## 1. 事实核验

- 只核验**能从材料内部或明确外部常识**判断的事实：数字、单位、名称、公式、术语是否自洽，前后是否一致。
- 无法在不联网的前提下确认的外部事实（具体论文的结论、机构、时间）**不擅自判定**，标 `pending_review`。
- 发现前后数字冲突时，两个来源都记录，标 `inconsistency`，不挑一个当"正确"。

## 2. 引用支持

- 检查"引文是否支撑所引的那句话"：引用处主张的强度是否超过被引文献通常能支撑的范围。
- **关键限制**：若拿不到被引文献全文，只能标 `citation_unsupported` 为 `probable` 或 `pending_review`，
  写"需外部核对"，不得断言"该引用不支持"。
- 引用是综述/转述而非作者结论时，区分 speaker，不误判。

## 3. 方法设计

- 检查研究问题 ↔ 方法 ↔ 结果之间的对应：方法能否回答研究问题。
- 典型缺陷（`method_mismatch`）：观察性设计却声称因果；样本/条件无法外推到结论范围；缺关键对照；
  变量定义与结果口径不一致。
- 对照组是**有意设置**的正常设计，不是缺陷；只有"缺该有的对照"才是问题。

## 4. 统计报告

- 检查效应量、置信区间、p 值、显著性方向、样本量与结论方向是否一致。
- 典型缺陷（`stat_misdirection`）：报告"不显著"却写成"无差异/等效"；方向写反；把相关性系数当因果证据；
  缺自由度/检验统计量导致不可复现。
- 只做"报告口径与结论是否一致"的审查，**不重算原始数据**（题目明确排除统计复算）。

## 5. 论证链

- 检查前提 → 结论是否有跳跃、隐含假设未说明、替代解释未排除、相关/因果混用。
- 典型缺陷：`overclaim`（结论强度超出证据）、`causal_from_correlational`、`evidence_gap`。
- 每条 finding 的 `reasoning` 必须回答三问（见 severity-rubric）。

## 6. 学术规范

- 检查重复发表、一稿多投、数据完整性与可获取性声明、利益冲突、伦理/知情同意、作者贡献等**可核验的规范问题**。
- 明确不做：查重判定、学术不端定性、参考文献格式规范（题目排除）。
- 只能报"材料内可证明"的规范问题，无法确认的标 `pending_review`。

## 7. 跨章节承诺—交付对照（单独做一遍）

- 把 `commitments[]` 逐条找结果/讨论里的交付位置，填 `delivered_at`（定位或 null）。
- 引言承诺分析 A，结果只给了 B → `missing_delivery`。
- 方法与结果口径不一致、摘要与正文结论不一致、图表与正文描述不一致 → `inconsistency`。

## 8. 防误判守则（正确性关键）

- 不把**对照组**报成缺陷：对照是设计，不是矛盾。
- 不把**明确假设**报成事实错误：假设情形要标 speaker=`assumption`。
- 不把**转述他人观点**报成作者自相矛盾：标 speaker=`cited`。
- 不把"材料没写"当成"作者错了"：缺证据是 `evidence_gap`/`pending_review`，不是 `overclaim`（除非结论强度确实超出所写证据）。
- 报 `overclaim` 前先问：作者是否已在限定条件里收窄了结论范围？若已收窄，则不是 overclaim。
- 宁可少报、标 `pending_review`，不可编造。每条 finding 都必须能被独立复核。

## 9. 何时标 pending_review / uncertainty

- 引用拿不到全文 → 该条 `pending_review`。
- 外部事实无法核验 → `pending_review` 或 `uncertainty`。
- 章节缺失、长文截断、扫描版无文本 → 写 `limitations`，不宣称已查全文。
- 两条证据冲突无法裁决 → 两条都记录并标 `source_conflict` 进 `uncertainty`。

## 10. 产出纪律

- findings 每条必填：`id / type / severity / locator / quote / claim / evidence / reasoning / suggestion / status / confidence`。
- `suggestion` 落到具体句子：补什么证据、改哪句、把哪个结论的强度降下来；禁止"建议加强论证"这类空话。
- `coverage` 六维逐维标 `done|partial|skipped` 与 `checked` 计数，没做的写 `skipped` 并说明原因。
