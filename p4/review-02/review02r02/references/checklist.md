# 引用与事实核验：判定规则与检查清单

本文件是执行审查时的判定基准，供 Agent 与人工复核共用。所有结论必须落到原文逐字
片段；无法确认时标 `needs_review` / `undeterminable`，不得补造。

## 1. 六类检查项（canonical key 与查什么）

| canonical key | 中文名 | 查什么 |
|---|---|---|
| `citation_support` | 引用支持判定 | 引用是否存在、引用是否**支持**该结论；区分"引用了"与"支持了" |
| `fact_source` | 事实核验 | 来源等级、出版状态、版本差异、更正/撤稿；预印本与正式版不一致 |
| `method_design` | 方法设计 | 方法描述、可复现性、对照组与统计方法交代是否充分 |
| `numerical_statistical` | 统计报告 | 摘要/正文/表/图注间数字、样本量、显著性表述是否自相矛盾 |
| `argumentation` | 论证链完整性 | 结论跳跃、循环论证、以偏概全、缺少对照 |
| `academic_norm` | 学术规范核查 | 署名、重复发表、数据与代码可用性、伦理与利益冲突声明 |

`category` 用上表 canonical key；六类都必须有覆盖或明确标注 `unchecked`。

## 2. 引用支持四档判定（核心）

| verdict | 含义 | 触发条件示例 |
|---|---|---|
| `fully_supported` | 完整支持 | 引文直接给出该结论的证据，语义一致 |
| `partially_supported` | 部分支持 | 引文只覆盖结论的一部分，或条件/对象不同 |
| `unsupported` | 不支持 | 引文不涉及、或与结论相反；结论超出引文范围 |
| `undeterminable` | 无法判定 | 只有摘要/无法取得原文，或引文信息不足 |

判定口径：比较**被引来源的语义**与**正文结论的语义**，而不是只看参考文献列表里
有没有这条文献。"结论超出引文范围"记为 `overreach`（归入 `unsupported` 或
`partially_supported`）。

## 3. finding_type 与严重度

`finding_type` 可选其一（优先级自上而下）：

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

严重度必须与问题性质匹配：不能用 `minor` 掩盖核心结论错误，也不能把格式建议标成
`critical`。

## 4. 证据定位与摘录规范

- `locator` 用可回查定位串：`p.3, para 2`、`Table 1, row 3`、`Fig 2b`、
  `materials/paper.md#L42`、`dataset entry: id=…`。PDF 抽取文本以
  `----- PAGE n / N -----` 标记页码，定位到该页与段。
- `quote` 必须**逐字**摘自原文（可用换行/删省略号 `…` 标记，但不改词、不增删关键
  数字）。`claim` 是对被审查断言的转述，须标注为转述，不得把转述当原文。
- `source_ref` 写被引用来源的定位（DOI/URL/参考文献编号/数据集条目），仅在确实取得
  该来源时填写；未取得写 `not_verified`。
- 同一指标两处不一致时，两条证据都记录，并标 `inconsistency`，不得只保留一方。

## 5. 数值与统计一致性检查清单

- 摘要、正文、表、图注中的 N、均值、标准差、置信区间、p 值是否一致。
- 百分比之和是否 100%（含四舍五入说明）；单位是否统一（%、mg、μg、mmol/L 等）。
- 显著性表述与统计量是否匹配（"显著"是否真有 p 值支撑、是否给出检验方法）。

## 6. 学术规范检查清单

- 作者/单位/通讯信息是否完整、是否疑似重复发表。
- 数据与代码可用性声明、伦理审批、利益冲突声明是否存在。
- 引用是否规范（引用但正文未出现、正文出现但参考文献缺失等）。

## 7. 诚实性底线

- 材料不足输出 `insufficient_evidence` / `verdict=insufficient`，列出缺口。
- 无法确认标 `needs_review` / `undeterminable`，写进 `uncertainty` 或 `pending_human`。
- 不虚构 DOI、URL、数值、来源、撤稿状态；缺失写 `not_reported` 或 `not_verified`。
- 不编造评测分数、运行结果或平台实测数据。

## 8. 判定与定位速查（复用模板）

### 8.1 locator 格式（按材料形态选择，必须可回查）

| 材料 | 推荐 locator | 反例（不可回查） |
|---|---|---|
| 电子全文 PDF | `p.3, para 2` 或 `p.3, para 2, sentence 1` | "文中某处" / "结果部分" |
| 表格 | `Table 1, row 3` 或 `Table 2, col "n"` | "表格里" |
| 图 | `Fig 2b` 或 `Fig 2, right panel` | "图上" |
| Markdown/纯文本 | `materials/paper.md#L42` | "前面" |
| 数据集 | `dataset entry: id=SUBJ-017` | "数据里" |

PDF 抽取文本以 `----- PAGE n / N -----` 标记页码；`locator` 里的页码须与此一致，
人工复核者打开原文同一页即可定位。

### 8.2 引用支持四档的语义对比口径

判定顺序：先确认"引用了什么"，再确认"结论说了什么"，最后比较两者语义范围。

- 引文结论 = 正文结论（对象、条件、方向一致）→ `fully_supported`
- 引文只覆盖结论的一部分 / 对象或条件不同 / 需要额外假设 → `partially_supported`
- 引文不涉及该结论 / 方向相反 / 结论所需证据超出引文 → `unsupported`（结论超出引文范围记 `overreach`）
- 拿不到引文原文 / 只有摘要 / 版本不清 → `undeterminable`

示例：正文写"药物 X 降低全因死亡率"，引文只报告"药物 X 降低某生物标志物"——
这是 `overreach`（`unsupported` 或 `partially_supported`），不是"引用了就等于支持了"。

### 8.3 严重度标定顺序（自上而下命中即止）

1. 会改变主结论，或构成学术不端（编造、撤稿未声明、关键结论无任何依据）→ `critical`
2. 显著影响可信度，需修改后结论才成立（数字矛盾、引用不支持核心结论、缺关键对照）→ `major`
3. 局部错误，不影响主结论（拼写、单位、引用格式、局部数字笔误）→ `minor`
4. 只提可改进项、不构成错误 → `suggestion`

无法在 1–3 之间判定时，宁可标 `major` 并写清依据，也不要用 `minor` 掩盖核心错误。
