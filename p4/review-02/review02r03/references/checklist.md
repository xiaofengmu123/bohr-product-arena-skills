# 引用与事实核验：判定规则与检查清单

本文件是执行审查时的判定基准，供 Agent 与人工复核共用。所有结论必须落到原文逐字片段；
无法确认时标 `needs_review`，不得补造。

## 1. 六类检查项（category canonical key）

| 类别 | canonical key | 查什么 |
|---|---|---|
| 事实核验 | `fact_check` | 可确证的事实/数值/术语错误；来源等级、出版状态、版本差异、更正与撤稿 |
| 引用支持 | `citation_support` | 引用是否存在、是否**支持**该结论；区分"引用了"与"支持了" |
| 方法设计 | `method_design` | 方法描述、可复现性、对照与统计方法的交代是否充分 |
| 统计报告 | `statistical_report` | 摘要/正文/表/图注间数字、样本量、显著性表述是否自相矛盾 |
| 论证链 | `argumentation` | 结论跳跃、循环论证、以偏概全、缺少对照 |
| 学术规范 | `academic_norm` | 署名、重复发表、数据与代码可用性、伦理与利益冲突声明 |

证据定位（`locator + quote`）是跨六类的硬性要求，不是独立类别：每条 finding 都必须能回查。

## 2. 引用支持四档判定（核心）

| verdict | 含义 | 触发条件示例 |
|---|---|---|
| `fully_supported` | 完整支持 | 引文直接给出该结论的证据，语义一致 |
| `partially_supported` | 部分支持 | 引文只覆盖结论一部分，或条件/对象不同 |
| `unsupported` | 不支持 | 引文不涉及、或与结论相反；结论超出引文范围 |
| `undeterminable` | 无法判定 | 只有摘要/原文取不到，或引文信息不足以判断 |

判定口径：比较**被引来源的语义**与**正文结论的语义**，而不是只看参考文献列表里有没有这条文献。
"结论超出引文范围"记为 `overreach`（`unsupported` 或 `partially_supported`）。
非 `citation_support` 类别的 finding 可用 `confirmed / needs_review / not_confirmed` 作为 verdict。

## 3. finding_type 与 severity

`finding_type`（可多选其一，优先级自上而下）：

- `factual_error`：可确证的事实/数值错误
- `inconsistency`：同一指标在文中不同位置自相矛盾
- `unsupported_claim`：断言无引用或引用不支持
- `overreach`：结论超出引文/数据范围
- `source_unreliable`：来源不可靠/版本冲突/撤稿未声明
- `missing_control`：缺对照/缺样本量/缺统计检验
- `norm_violation`：署名/重复发表/伦理/利益冲突等规范问题
- `evidence_insufficient`：材料不足以支持，需补证据
- `suggestion`：建议性改进，不构成错误

`severity` 四档：

| severity | 定义 | 例子 |
|---|---|---|
| `critical` | 会改变结论或构成学术不端 | 编造数据、关键结论无依据、撤稿未声明 |
| `major` | 显著影响可信度，需修改后结论才成立 | 数字矛盾、引用不支持核心结论 |
| `minor` | 局部错误，不影响主结论 | 拼写/单位/引用格式错误 |
| `suggestion` | 可改进项 | 建议补充敏感性分析、增加可用性说明 |

严重度必须与问题性质匹配：不能用 `minor` 掩盖核心结论错误，也不能把格式建议标成 `critical`。

## 4. 证据定位与摘录规范

- `locator` 用可回查定位串：`p.3, para 2`、`Table 1, row 3`、`Fig 2b`、
  `materials/paper.md#L42`、`dataset entry: id=…`。PDF 抽取文本以
  `----- PAGE n / N -----` 标记页码，定位到该页与段。
- `quote` 必须**逐字**摘自原文（可换行/删省略号标记，但不改词、不增删关键数字）。
- `claim` 是对被审查断言的转述，须标注为转述；不得把转述当原文。
- `source_ref` 写被引用来源的定位（DOI/URL/参考文献编号/数据集条目），仅在确实
  取得该来源时填写；未取得写 `not_verified`。
- 同一指标两处不一致时，两条证据都记录并标 `inconsistency`，不得只保留一方。

## 5. 数值与统计一致性检查清单

- 摘要、正文、表、图注中的 N、均值、标准差、置信区间、p 值是否一致。
- 百分比之和是否 100%（含四舍五入说明）。
- 单位是否统一（%，mg，μg，mmol/L 等）。
- 显著性表述与统计量是否匹配（"显著"是否真的有 p 值支撑、是否给出检验方法）。

## 6. 学术规范检查清单

- 作者/单位/通讯信息是否完整、是否疑似重复发表。
- 数据与代码可用性声明、伦理审批、利益冲突声明是否存在。
- 引用是否规范（格式、正文未出现但参考文献存在、正文出现但参考文献缺失）。

## 7. 诚实性底线

- 材料不足输出 `insufficient_evidence` / `verdict=insufficient`，列出缺口。
- 无法确认标 `needs_review` / `undeterminable`，写进 `uncertainty` 或 `pending_human`。
- 不虚构 DOI、URL、数值、来源、撤稿状态；缺失写 `not_reported` 或 `not_verified`。
- 不编造评测分数、运行结果或正式案例答案。
