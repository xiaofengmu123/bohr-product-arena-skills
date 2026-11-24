# 判定规则与检查清单（证据溯源强化版）

本文件是执行审查时的判定基准，供 Agent 与人工复核共用。所有结论必须落到原文
逐字片段；无法确认标 `needs_review` / `undeterminable`，不得补造。

## 1. 七类检查项

| 类别 | canonical key | 查什么 |
|---|---|---|
| 引用支持判定 | `citation_support` | 引用是否存在、引用是否**支持**结论；区分"引用了"与"支持了" |
| 事实来源可靠性 | `fact_source` | 来源等级、出版状态、版本差异、更正/撤稿；预印本与正式版不一致 |
| 证据定位 | `evidence_location` | 结论能否落到页/段/表/图/数据集条目，定位串可回查 |
| 数值与统计一致性 | `numerical_statistical` | 摘要/正文/表格/图注之间数字、样本量、显著性表述是否自相矛盾 |
| 论证链完整性 | `argumentation` | 结论跳跃、循环论证、以偏概全、缺少对照 |
| 学术规范核查 | `academic_norm` | 署名、重复发表、数据与代码可用性、伦理与利益冲突声明 |
| 方法设计（补充） | `method_design` | 方法描述、可复现性、对照组与统计方法交代是否充分 |

## 2. 引用支持四档判定（核心）

| verdict | 含义 | 触发条件示例 |
|---|---|---|
| `fully_supported` | 完整支持 | 引文直接给出该结论的证据，语义一致 |
| `partially_supported` | 部分支持 | 引文只覆盖结论一部分，或条件/对象不同 |
| `unsupported` | 不支持 | 引文不涉及、或与结论相反；结论超出引文范围 |
| `undeterminable` | 无法判定 | 只有摘要/无法取得原文，或引文信息不足 |

判定口径：比较**被引来源的语义**与**正文结论的语义**，而不是只看参考文献列表。
"结论超出引文范围"记为 `overreach`（`unsupported` 或 `partially_supported`）。

## 3. finding_type 与严重度

`finding_type`：

- `factual_error` 可确证的事实/数值错误
- `inconsistency` 同一指标在文中自相矛盾
- `unsupported_claim` 断言无引用或引用不支持
- `overreach` 结论超出引文/数据范围
- `source_unreliable` 来源不可靠/版本冲突/撤稿未声明
- `missing_control` 缺对照/缺样本量/缺统计检验
- `norm_violation` 署名/重复发表/伦理/利益冲突等规范问题
- `evidence_insufficient` 材料不足以支持，需补证据
- `suggestion` 建议性改进，不构成错误

`severity` 分四档：`critical`（改变结论或学术不端）、`major`（显著影响可信度）、
`minor`（局部错误，不影响主结论）、`suggestion`（可改进项）。严重度必须与问题
性质匹配：不得用 `minor` 掩盖核心结论错误，也不得把格式建议标成 `critical`。

## 4. 证据定位与摘录规范（本版强化）

- `locator` 优先结构化对象（见 result-schema.md §5），`raw` 保留可读串。
- `quote` 必须**逐字**摘自原文；可换行/删省略号，但不改词、不增删关键数字。
- `claim` 是对被审断言的转述，须与原文 `quote` 明确区分。
- 每条 finding 至少一条 `primary_support` 证据；有反面证据或关键上下文时用
  `evidence` 数组追加 `counter_evidence` / `context`，脚本自动生成确定性
  `evidence_id` 与 `quote_hash`。
- 同一指标两处不一致时，两条证据都记录并标 `inconsistency`，进 `conflicts`，
  不得只保留一方。
- `source_ref` 仅在确实取得该来源时填写；未取得写 `not_verified`；缺失写
  `not_reported`。

## 5. 数值与统计一致性清单

- 摘要、正文、表、图注中的 N、均值、标准差、置信区间、p 值是否一致。
- 百分比之和是否 100%（含四舍五入说明）。
- 单位是否统一（%、mg、μg、mmol/L 等）。
- 显著性表述与统计量是否匹配（"显著"是否有 p 值支撑、是否给出检验方法）。

## 6. 学术规范清单

- 作者/单位/通讯信息是否完整、是否疑似重复发表。
- 数据与代码可用性、伦理审批、利益冲突声明是否存在。
- 引用是否规范：参考文献存在但正文未出现、正文出现但参考文献缺失。

## 7. 提交前自检（逐条可勾选，对应评分维度）

实际效果：

- [ ] 每条 finding 有 `id/category/locator/quote/verdict/severity/suggestion`。
- [ ] 每条结论能按 `locator + quote` 回查原文，`quote` 逐字。
- [ ] 判定与引文语义一致，"引用存在"与"引用支持"已区分。
- [ ] 七类检查项与方法/结果/讨论等关键章节均有覆盖或明确标注未覆盖。

使用增益：

- [ ] 同一材料、同一证据下结论稳定；`finalize` 重复运行结果一致（无随机数）。
- [ ] evidence_map 中每条有 role/quote_hash/source_status，冲突双侧保留。

可靠体验：

- [ ] `--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。
- [ ] 空输入、坏 JSON、坏编码、缺 pdftotext、扫描版 PDF 均降级不崩溃。
- [ ] 前 3 分钟内已落盘 result.json 骨架，之后只做增量改进。

生态贡献：

- [ ] `scripts/*.py` 只用标准库、可直接运行、`--help` 退出码 0。
- [ ] 说明、依赖、适用范围、限制、失败处理与最小调用示例齐全。

## 8. 诚实性底线

- 材料不足输出 `insufficient_evidence` / `verdict=insufficient`，列出缺口。
- 无法确认标 `needs_review` / `undeterminable`，写进 `uncertainty` 或
  `pending_human`。
- 不虚构 DOI、URL、数值、来源、撤稿状态；缺失写 `not_reported` 或 `not_verified`。
