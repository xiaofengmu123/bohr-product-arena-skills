# 引用与事实核验：判定规则与检查清单

本文件是执行审查时的判定基准，供 Agent 与人工复核共用。所有结论必须落到原文
逐字片段；无法确认时标 `needs_review` / `undeterminable`，不得补造。

## 1. 审查类别（canonical key）

| 类别 | canonical key | 查什么 |
|---|---|---|
| 引用支持判定 | `citation_support` | 引用是否存在、引用是否**支持**该结论；区分"引用了"与"支持了" |
| 事实来源可靠性 | `fact_source` | 来源等级、出版状态、版本差异、更正/撤稿；预印本与正式版不一致 |
| 证据定位 | `evidence_location` | 结论能否落到页/段/表/图/数据集条目，定位串可回查 |
| 数值与统计一致性 | `numerical_statistical` | 摘要/正文/表格/图注之间的数字、样本量、显著性表述是否自相矛盾 |
| 论证链完整性 | `argumentation` | 结论跳跃、循环论证、以偏概全、缺少对照 |
| 学术规范核查 | `academic_norm` | 署名、重复发表、数据与代码可用性、伦理与利益冲突声明 |
| 方法设计（补充） | `method_design` | 方法描述、可复现性、对照组与统计方法交代是否充分 |

`category` 用上表 canonical key；`finding_type` 用 §3 的细分类。

## 2. 引用支持四档判定（核心）

| verdict | 含义 | 触发条件示例 |
|---|---|---|
| `fully_supported` | 完整支持 | 引文直接给出该结论的证据，语义一致 |
| `partially_supported` | 部分支持 | 引文只覆盖结论的一部分，或条件/对象不同 |
| `unsupported` | 不支持 | 引文不涉及、或与结论相反；结论超出引文范围 |
| `undeterminable` | 无法判定 | 只有摘要/无法取得原文，或引文信息不足 |

判定口径：比较**被引来源的语义**与**正文结论的语义**，而不是只看参考文献列表
里有没有这条文献。"结论超出引文范围"记为 `overreach`（`unsupported` 或
`partially_supported`）。"引用了"只证明文献出现在列表里；"支持了"要求引文
内容确实支撑该结论——这是本题最重要的区分。

## 3. finding_type 与严重度

`finding_type`（每条 finding 选一个主类型，优先级自上而下）：

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

严重度必须与问题性质匹配：不能用 `minor` 掩盖核心结论错误，也不能把格式建议
标成 `critical`。

## 4. 证据定位与摘录规范

- `locator` 用可回查的定位串：`p.3, para 2`、`Table 1, row 3`、`Fig 2b`、
  `materials/paper.md#L42`、`dataset entry: id=…`。PDF 抽取文本以
  `----- PAGE n / N -----` 标记页码，定位到该页与段。
- `quote` 必须**逐字**摘自原文（可换行/删省略号标记，但不改词、不增删关键数字）。
- `claim` 是对被审查断言的转述，须标注为转述；不得把转述当原文。
- `source_ref` 写被引用来源的定位（DOI/URL/参考文献编号/数据集条目），仅在确实
  取得该来源时填写；未取得写 `not_verified`。
- 同一指标两处不一致时，两条证据都记录，并标 `inconsistency`，不得只保留一方。

## 5. 数值与统计一致性检查清单

- 摘要、正文、表、图注中的 N、均值、标准差、置信区间、p 值是否一致。
- 百分比之和是否 100%（含四舍五入说明）。
- 单位是否统一（%, mg, μg, mmol/L 等）。
- 显著性表述与统计量是否匹配（"显著"是否真的有 p 值支撑、是否给出检验方法）。
- 正文与表格/图注之间的样本量、亚组人数、随访时间是否互相矛盾。

## 6. 学术规范检查清单

- 作者/单位/通讯信息是否完整、是否疑似重复发表。
- 数据与代码可用性声明、伦理审批、利益冲突声明是否存在。
- 引用是否规范（格式、是否存在引用但正文未出现、或正文出现但参考文献缺失）。
- 预印本与正式版版本差异、更正/撤稿状态是否在文中如实交代。

## 7. 证据先行的中间表示约定（本 Skill 的核心设计）

审查不是"边读边直接写结论"，而是两步：

1. **抽取**：把正文中的候选断言/引用/数值/方法陈述逐条写进
   `evidence/<EV-id>.json`，每条只含原始定位与逐字摘录，不先下结论。
2. **判定**：在 `findings/<F-id>.json` 中引用一个或多个 evidence id，给出
   category / finding_type / severity / verdict / suggestion。

这样每条结论都可被第二个人按 `evidence_ids → locator + quote` 回查，且证据去重
后可在多个 finding 间复用。`scripts/main.py finalize` 会把两者合并成
`audit_model.json`（唯一中间事实源），再渲染 `result.json` 与 `report.md`。

## 8. 提交前自检（逐条可勾选，对应评分维度）

实际效果：

- [ ] 每条 finding 都有 `id/category/locator/quote/verdict/severity/suggestion`。
- [ ] 每条结论能按 `evidence_ids → locator + quote` 回查原文，`quote` 为逐字摘录。
- [ ] 判定与引文语义一致，"引用存在"与"引用支持"已区分。
- [ ] 六类检查项与方法/结果/讨论等关键章节均有覆盖或明确标注未覆盖。

使用增益：

- [ ] 同一材料、同一证据下结论稳定；重复运行 finalize 不改变结论（无随机数）。
- [ ] 定位串、原文摘录、来源、判定依据齐全，人工可低返工复核。

可靠体验：

- [ ] `--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。
- [ ] 空输入、坏 JSON、坏编码、缺 pdftotext、扫描版 PDF 均能降级不崩溃。
- [ ] 前 3 分钟内已落盘 result.json 骨架，之后只做增量改进。

生态贡献：

- [ ] `scripts/*.py` 只用标准库、可直接运行、`--help` 退出码 0。
- [ ] 说明、依赖、适用范围、限制、失败处理与最小调用示例齐全。

## 9. 诚实性底线

- 材料不足输出 `insufficient_evidence` / `verdict=insufficient`，列出缺口。
- 无法确认标 `needs_review` / `undeterminable`，写进 `uncertainty` 或 `pending_human`。
- 不虚构 DOI、URL、数值、来源、撤稿状态；缺失写 `not_reported` 或 `not_verified`。
- 严禁编造评测分数与运行结果；平台记录真实预算、运行时与轨迹。
