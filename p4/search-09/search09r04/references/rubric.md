# 评价量表与判定规则（litqa-veritas）

本文件定义四个通用维度、文献类型特有检查点、证据充分性阶梯、分级阈值、不确定性分类与降级矩阵。Agent 逐条判定时以此为准；任何规则未覆盖处写 `pending_human`，不自行放宽。

## 1. 四类通用维度

| 维度 | 关注点 | 示例检查点 |
|---|---|---|
| 研究设计（design） | 设计类型是否支持结论；是否声明研究问题/对象/干预或暴露/对照或比较/结局与时间点 | 设计类型识别、对照设置、随机化/盲法（适用时）、样本量依据 |
| 证据充分性（evidence） | 样本、测量、统计口径是否站得住 | 样本量与脱落、效应量+置信区间、缺失数据处理、敏感性分析、检验假设 |
| 偏倚风险（bias） | 偏倚发生在哪些具体环节 | 选择偏倚、信息/测量偏倚、混杂、报告偏倚、失访/依从偏倚 |
| 报告完整性（reporting） | 是否缺项、是否可复现 | 数据可得性、代码/协议、登记与预注册、资助与利益冲突、图表与正文一致性 |

## 2. 文献类型特有检查点

- 随机对照试验：随机序列生成、分配隐藏、盲法、ITT 分析、预注册结局、选择性报告。
- 观察性研究：暴露与结局定义、混杂控制、时相、失访、剂量-反应、灵敏度分析。
- 系统评价/Meta 分析：检索式与数据库、纳入排除标准、双重筛选、偏倚风险工具、异质性、发表偏倚。
- 诊断/预后研究：金标准、盲法评估、阈值预设、校准与区分度、验证方式。
- 定性研究：抽样策略、资料饱和、编码信度、反身性、转移性说明。
- 不可归类时：至少覆盖四类通用维度，并在 `limitations` 说明缺少类型特化量表。

## 3. 证据充分性阶梯（每条判断必标）

| `evidence_level` | 判定条件 | 允许产出 |
|---|---|---|
| `direct` | `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 可核验） | 判断 + 逐字 quote |
| `paraphrase` | 判断由所标位置支持，但文字非逐字 | 判断 + `basis`（依据句） |
| `inference` | 由所标位置按写明的推理规则推出 | 判断 + `basis` + 规则（写进 `judgment` 或 `basis`） |
| `absent` | 原文未报告该证据 | 仅 `verdict=insufficient_evidence`，不写实质判断 |

弃权规则：`critical=true` 且证据为 `absent` 的检查点，其维度结论不得正向化；该文献整体等级上限 `insufficient_evidence`。非关键条目 `absent` 记 `unclear` 并写入 `limitations`。

## 4. 偏倚风险四档判定

- `low_risk`：明确报告了控制措施且有直接证据。
- `some_concerns`：措施部分报告，或仅推断/转述。
- `high_risk`：明确报告未控制，或存在明确偏倚信号。
- `critical_risk`：偏倚足以推翻该维度结论。
- 证据不足时用 `insufficient_evidence`，不得写成低风险。

## 5. 分级阈值（`grade` 汇总）

| 等级 | 判定条件 |
|---|---|
| `low` | 关键检查点全有 `direct/paraphrase` 证据，且无 `high/critical` 偏倚信号 |
| `moderate` | 存在 `moderate` 偏倚或个别 `inference`，但关键结论仍被直接支持 |
| `high` | 存在 `high` 偏倚信号，或多个关键检查点仅 `inference` |
| `unclear` | 关键证据缺失但非关键部分可得，或来源冲突未裁决 |
| `insufficient_evidence` | 关键检查点证据 `absent`，或全文不可得 |

汇总必须写明"该等级由哪些维度/条目如何汇总而来"；不允许只给孤立的等级标签。

## 6. 不确定性模型（`uncertainty` 数组）

每条含以下字段：

| 字段 | 取值/含义 |
|---|---|
| `id` | 稳定编号，如 `unc1` |
| `kind` | `evidential`（证据缺口）/ `methodological`（方法局限）/ `conflict`（来源冲突）/ `access`（不可获取）/ `inference`（推理不确定） |
| `level` | `low` / `moderate` / `high` / `critical` |
| `scope` | 影响的检查点/维度/结论，如 `assessment[3].verdict` 或 `overall_grade` |
| `statement` | 不确定什么（具体，非泛泛） |
| `evidence_gap` | 缺口证据是什么 |
| `mitigation` | 已采取的缓解措施 |
| `status` | `open` / `resolved_by_degradation` / `pending_human` |

传播规则：`critical` 不确定性影响的维度与整体结论必须相应降级；`conflict` 指标等级至少 `unclear`。

## 7. 降级矩阵（缺数据/缺软件 → 必须落到的字段与 status）

| 情形 | `limitations` | `uncertainty` | `degradations` | `status` 下限 |
|---|---|---|---|---|
| 全文不可得/仅摘要 | 记录缺口 | `kind=access` | 无 | `insufficient_evidence` |
| PDF 无法抽取文本 | 记录 | `kind=access` | 记"未抽取文本" | `partial` |
| 图表不可解析 | 记录 | `kind=evidential` | 记图表缺口 | `partial` |
| 关键统计量未报告 | 记录 | `kind=evidential`, 标 `evidence_gap` | 无 | `partial` |
| 预印本/语言特殊 | 记录 | `kind=methodological` | 记特殊性 | `partial` |
| 来源冲突 | 记录 | `kind=conflict` | 无 | `partial` 或按需 |
| 候选集为空 | 记录 | `kind=access`/`evidential` | 记空候选 | `insufficient_evidence` |
| 缺外部软件（非标准库） | 记录 | `kind=access` | 记缺失工具 | `partial` |

任何降级都要写清"已完成与未完成部分"，不补造数据或结论。
