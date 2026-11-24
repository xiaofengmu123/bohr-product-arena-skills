# 六类检查项清单与防误判守则

审查目标是"主张与证据之间是否断裂"，不是复述论文。每报一条 finding，必须能回到原文某一句话，并给出可执行的修改建议。

## 0. 主张抽取与 speaker 区分（先做，贯穿全程）

先建主张清单，逐条登记：

- `author`：作者自己的主张/结论；
- `cited`：作者转述他人观点或文献结论（引用式表达，如"X 等报道…"）；
- `assumption`：明确标注的假设/前提（如"假设…""若…"）；
- `control`：对照组、基线、消融设置等有意设计。

**防误判守则**：不得把 `cited` 的观点当作作者自相矛盾；不得把 `assumption` 当作事实断言；不得把 `control` 的存在当作"缺少实验"。

## 1. 事实核验（factual_verification）

- 数值、单位、定义、术语与后文或表格是否一致；是否存在同义指标两种口径。
- 摘要/结论的数字是否与结果/图表一致。
- 无法外部核验的断言标 `pending_review`，写"需外部核对"，不得断言"错误"。

## 2. 引用支持（citation_support）

- 引文是否真的支持它所支撑的那句话；区分"该文献有类似结论"与"该文献直接支持该强度表述"。
- 引用式转述是否保留了原意与限定条件。
- 拿不到全文时标 `pending_review`，不编造"引用不支持"。

## 3. 方法设计（method_design）

- 方法能否回答研究问题；样本/对象/测量是否与结论范围匹配。
- 对照、盲法、重复、排除标准是否说明；缺失即记录，不臆测。

## 4. 统计报告（statistical_reporting）

- 效应量、置信区间、p 值、样本量、多重比较校正是否报告。
- 统计口径与结论方向是否一致（如"显著改善"是否与效应方向一致）。
- 相关性设计是否被用因果语言表述。

## 5. 论证链（argument_chain）

- 前提→结论是否存在跳跃、隐含假设未说明、替代解释未排除。
- 限定条件在结论中是否被省略（外推范围是否超出数据）。

## 6. 学术规范（academic_norms）

- 数据/材料可得性、伦理/利益冲突、作者贡献、预注册等声明（按学科惯例）。
- 不做查重、学术不端判定、参考文献格式检查（题目明确排除）。

## 严重度分级（可解释）

| severity | 判据（三问） |
|---|---|
| critical | 影响主结论成立，且难以局部修正 |
| major | 影响主结论强度，或影响可复现性，但可修正 |
| minor | 不影响主结论，但影响某一句/某表准确性 |
| suggestion | 建议性意见，不构成错误 |

每条 reasoning 必须回答三问：是否影响主结论 / 是否可修正 / 是否影响可复现。不得一律标"中"。

## finding 必填字段

`id, type, severity, locator, quote, claim, evidence, reasoning, suggestion, status, confidence`

- `locator`：`p页 ¶P段落` 或 `¶P段落`，与 `work/corpus.md` 一致；
- `quote`：逐字原文摘录，不得改写；
- `status`：`confirmed | probable | pending_review | ruled_out`；
- `confidence`：`high | medium | low`；
- `type`：`overclaim | unsupported | citation_gap | method_mismatch | stat_mismatch | logic_gap | inconsistency | commitment_unmet | norm_gap`（可扩展，但需自解释）。
