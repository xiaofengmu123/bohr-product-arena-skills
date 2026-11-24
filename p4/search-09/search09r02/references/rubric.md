# 评价量表与分级规则（rubric）

本文件是 `litqa-staged` 的判据唯一事实源。脚本 `scripts/litqa.py` 的 `grade` 子命令与本表阈值保持一致；任何改动需两处同步。

## 1. 四元结构：维度—判断—证据—等级

每个评价条目（`assessment[]`）由以下字段组成，缺一不可：

| 字段 | 含义 | 取值约束 |
|---|---|---|
| `dimension` | 维度 | `研究设计` / `证据充分性` / `偏倚风险` / `报告完整性`（四类必覆盖） |
| `checkpoint` | 检查点 | 见 §3、§4；命名须稳定、可被双人复用 |
| `critical` | 是否关键检查点 | `true/false` |
| `polarity` | 极性 | `favorable`（质量属性）/ `adverse`（风险/缺陷） |
| `verdict` | 判定 | `confirmed/partial/absent/insufficient/not_applicable`（见 §2） |
| `judgment` | 判断 | 一句话结论，陈述"本文做了什么/未做什么/无法判断" |
| `locator` | 证据定位 | `{file_id}#p{N}`、`{file_id}#p{N}s{M}` 或 `{file_id}#line{N}` |
| `quote` | 逐字引文 | 规范化后须逐字存在于所标 locator；弃权时为空 |
| `basis` | 证据来源类型 | `fulltext/methods/results/abstract/metadata/reference_graph` |
| `source_id` | 材料标识 | 与 `sources[].id` 一致（即 `locators.json` 生成的 file_id） |

判定不是摘要改写：`judgment` 必须回答该检查点在本文献中的具体表现，且能由 `locator + quote` 回溯。

## 2. 判定值（verdict）与弃权规则

**极性 × 判定的语义**（这是弃权规则的核心：证据不足就写不足，不补缺）：

| verdict | favorable（质量属性） | adverse（偏倚/缺陷） |
|---|---|---|
| `confirmed` | 报告充分/实施到位，原文可定位 | **明确存在**：原文有直接证据 |
| `partial` | 部分报告/间接证据 | **可能存在**：间接证据或方法描述不足 |
| `absent` | 未报告/未做（本身是缺口发现） | **未见迹象**：检索后未发现该偏倚信号 |
| `insufficient` | **证据不足**：材料不足以判断（弃权） | **证据不足**：材料不足以判断（弃权） |
| `not_applicable` | 该检查点不适用本文献类型 | 同左 |

弃权规则：

1. 只有摘要、无全文/方法/结果时，凡依赖方法或结果的检查点判 `insufficient`，不猜。
2. 图表无法解析时，凡依赖图表数值的检查点判 `insufficient` 并记入 `limitations`。
3. 预印本/语言障碍/关键章节缺失：对应检查点判 `insufficient` 并在 `degradations` 注明原因。
4. `quote` 与 `locator` 必须成对：弃权时两者都为空；有引文必须有可定位的位置。

## 3. 四类维度与检查点

`critical=true` 的检查点为核心，`polarity` 见括号。

### 3.1 研究设计

- 研究问题与假设明确（critical, favorable）
- 研究设计类型正确识别：RCT/队列/病例对照/横断面/系统综述/诊断/定性等（critical, neutral）
- 对照设置合理（critical, favorable）
- 纳入/排除标准明确（critical, favorable）
- 样本量与把握度计算（critical, favorable）
- 干预/暴露定义清晰（favorable）
- 伦理审批与知情同意（favorable）

### 3.2 证据充分性

- 样本来源与抽样方法（critical, favorable）
- 样本量及流失/脱落（critical, favorable）
- 结局指标定义与测量时点（critical, favorable）
- 数据完整性与缺失处理（critical, favorable）
- 效应量、置信区间与精度（critical, favorable）
- 随访时长与完成率（favorable）
- 亚组/敏感性分析（favorable）

### 3.3 偏倚风险（均 adverse）

- 选择偏倚（critical, adverse）
- 失访/不完整结局偏倚（critical, adverse）
- 选择性报告偏倚（critical, adverse）
- 混杂控制（critical, adverse）
- 实施/性能偏倚（adverse）
- 测量/检出偏倚（adverse）
- 发表偏倚与利益冲突（adverse）

### 3.4 报告完整性

- 摘要与结论一致（critical, favorable）
- 方法可复现性（critical, favorable）
- 全部预设结局均有结果（critical, favorable）
- 局限性声明（favorable）
- 资金来源与利益冲突声明（favorable）
- 数据/代码可及性（favorable）
- 报告清单（CONSORT/STROBE/PRISMA 等）遵守（favorable）

## 4. 文献类型特有检查点（按类型追加）

| 文献类型 | 追加关键检查点 |
|---|---|
| 系统综述/Meta | 检索策略与数据库齐全；研究纳入/排除流程（PRISMA）；纳入研究质量评价；异质性（I²）与合并模型（固定/随机）；发表偏倚评估 |
| 随机对照试验（RCT） | 随机化方法与分配隐蔽；盲法实施；ITT 分析；CONSORT 遵守 |
| 观察性研究（队列/病例对照/横断面） | 暴露与结局测量；混杂控制策略；随访完整性；STROBE 遵守 |
| 诊断准确性研究 | 金标准定义；盲法独立判读；诊断准确性指标（灵敏度/特异度/LR）；STARD 遵守 |
| 预实验/初步研究 | 样本量有限声明；效应量而非显著性强调；外推边界说明 |

文献类型由 `研究设计` 维度识别后确定追加集合；识别错误时在 `uncertainty` 记录。

## 5. 证据优先顺序与来源

同一检查点的证据按可信度取用，并在 `basis` 标注：

`fulltext（方法/结果） > 正文其他章节 > reference_graph（引文/被引） > metadata（元数据） > abstract`

- 高优先级证据可直接支撑判定；低优先级证据只能支撑 `partial` 或 `insufficient` 说明。
- 同一指标多来源不一致时，全部记录到 `conflicts` 并标 `source_conflict`，不隐藏、不择一。

## 6. 分级阈值（与 `scripts/litqa.py grade` 一致）

每个维度独立分级，再汇总总体。符号：

- `CAc` = critical + adverse + `confirmed`（关键偏倚明确存在）
- `CAp` = critical + adverse + `partial`（关键偏倚可能存在）
- `CI` = critical + `insufficient`（关键检查点证据不足）
- `CFa` = critical + favorable + `absent`（关键质量属性缺失）
- `NAc` = 非 critical + adverse + `confirmed`（非关键偏倚明确存在）
- `CE` = 已评价的 critical 数（verdict 非 `insufficient`/`not_applicable`）
- `clean` = "干净"的 critical 数：favorable/neutral 且 `confirmed`，或 adverse 且 `absent`
- `n_insuf` = `insufficient` 总数；`n_eval` = 已评价（非 `not_applicable`）总数

**维度等级**：

| 等级 | 条件（自上而下优先命中） |
|---|---|
| `high` | `CAc ≥ 1` |
| `medium` | `CAp ≥ 1` 或 `CI ≥ 1` 或 `CFa ≥ 1` 或 `NAc ≥ 1`；或（无上述触发但）`CE > 0` 且 `clean < CE`（存在未达标的 critical，如关键质量属性仅 partial） |
| `low` | `CE > 0` 且 `clean == CE`（全部关键检查点干净）；或 `CE == 0` 且 `n_eval > 0`（无 critical，且无 high/medium 触发） |
| `insufficient_evidence` | `n_insuf > 0.5 × n_eval`，或 `n_eval == 0` |

**总体等级**：取各维度最差（`low < medium < high`）；若超半数维度为 `insufficient_evidence`，总体记 `insufficient_evidence`。

等级必须携带理由：`grades.summary.dimensions[维度].stats` 记录上述计数，`judgment` 记录定性理由，不允许只给孤立标签。

## 7. 适用文献类型、依赖与限制

- 适用：单一文献或小批文献的研究设计与报告质量评价、偏倚风险定位、可追溯候选集与筛选依据；研究设计类型限于 §4 所列（扩展到其他类型需先补检查点表）。
- 不适用：替代同行评审、事实核验、临床/政策决策、研究结论本身真伪、跨学科质量统一排名。
- 依赖：仅 Python 标准库；文本抽取依赖 `pdftotext`（缺失时保留原始 PDF 并记 `degradations`）。不做 live 检索——候选集只来自本地提供的 Crossref/EuropePMC/publisher 响应与引用图，杜绝虚构全文/引文/状态声明。
- 限制：`locators` 的句子切分为启发式（中英文句末标点），对密集公式/表格文本可能漂移；表格数值须人工定位，脚本只对文本逐字核验。

## 8. 相对已有方案的边界

- 相对通用摘要：本表按量表逐检查点判定并锚定证据，不是"设计合理、样本适中"式复述。
- 相对公开报告清单（CONSORT/STROBE/PRISMA）：本表把清单条目映射为带极性、critical 标记与证据锚定的可评分项，并叠加偏倚风险与分级阈值。
- 相对期刊编辑初筛：本表不输出"收/拒"结论，输出"可复核的维度判定 + 分级理由 + 缺口清单"。
