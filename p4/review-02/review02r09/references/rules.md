# 引用与事实核验：判定规则与 result.json 契约（精简合并版）

本文件供 Agent 执行与人工复核共用。所有结论必须落到原文逐字片段；无法确认时标
`needs_review`，不得补造。以下为唯一判定基准，SKILL.md 不再重复展开。

## 1. 六类检查项（finding.category 的 canonical key）

| category | 查什么 |
|---|---|
| `citation_support` | 引用是否存在、是否**支持**该结论；区分"引用了"与"支持了" |
| `fact_source` | 来源等级、出版状态、版本差异、更正/撤稿；预印本与正式版不一致 |
| `evidence_location` | 结论能否落到页/段/表/图/数据集条目，定位串可回查 |
| `numerical_statistical` | 摘要/正文/表/图注之间数字、样本量、显著性表述是否自相矛盾 |
| `argumentation` | 结论跳跃、循环论证、以偏概全、缺少对照 |
| `academic_norm` | 署名、重复发表、数据与代码可用性、伦理与利益冲突声明 |
| `method_design` | 方法描述、可复现性、对照组与统计方法是否充分（补充类） |

## 2. 引用支持四档判定（finding.verdict）

| verdict | 含义 | 触发条件示例 |
|---|---|---|
| `fully_supported` | 完整支持 | 引文直接给出该结论证据，语义一致 |
| `partially_supported` | 部分支持 | 引文只覆盖一部分，或条件/对象不同 |
| `unsupported` | 不支持 | 引文不涉及或与结论相反；结论超出引文范围 |
| `undeterminable` | 无法判定 | 只有摘要/取不到原文/引文信息不足 |

判定口径：比较**被引来源语义**与**正文结论语义**，而非只看参考文献列表里有没有这条
文献。"结论超出引文范围"记 `finding_type=overreach`（verdict 为 `unsupported` 或
`partially_supported`）。

## 3. finding_type 与 severity

`finding_type`（细分类）：`factual_error`（可确证的事实/数值错误）、`inconsistency`
（同一指标自相矛盾）、`unsupported_claim`（无引用或引用不支持）、`overreach`（超出
引文/数据范围）、`source_unreliable`（来源不可靠/版本冲突/撤稿未声明）、
`missing_control`（缺对照/样本量/统计检验）、`norm_violation`（署名/重复发表/伦理/
利益冲突）、`evidence_insufficient`（材料不足，需补证据）、`suggestion`（建议性改进）。

`severity` 四档：

| severity | 定义 | 例子 |
|---|---|---|
| `critical` | 会改变结论或构成学术不端 | 编造数据、关键结论无依据、撤稿未声明 |
| `major` | 显著影响可信度 | 数字矛盾、引用不支持核心结论 |
| `minor` | 局部错误，不影响主结论 | 拼写/单位/引用格式 |
| `suggestion` | 可改进项 | 建议补敏感性分析、可用性说明 |

严重度必须与问题性质匹配：核心结论错误不标 `minor`，格式建议不标 `critical`。

## 4. 证据规范

- `locator` 用可回查定位串：`p.3, para 2`、`Table 1, row 3`、`Fig 2b`、
  `materials/paper.md#L42`、`dataset entry: id=…`。PDF 抽取文本以
  `----- PAGE n / N -----` 标记页码，定位到该页与段。
- `quote` 必须**逐字**摘自原文（可换行/省略号标记，但不改词、不增删关键数字）。
- `claim` 是对被审查断言的转述，须标注为转述，不得当原文。
- `source_ref` 写被引用来源定位（DOI/URL/参考文献编号/数据集条目），仅在实际取得
  该来源时填写；未取得写 `not_verified`。
- 同一指标两处不一致：两条证据都记录并标 `inconsistency`，不得只保留一方。

## 5. result.json 契约

顶层字段（`scaffold` 自动生成，`finalize` 合并刷新）：

| 字段 | 取值/说明 |
|---|---|
| `case_id` | 案例标识（缺省 `unknown`） |
| `status` | `ok` \| `partial` \| `insufficient_evidence` \| `input_error` |
| `summary.verdict` | `pass` \| `issues_found` \| `needs_review` \| `unchecked` \| `insufficient` |
| `summary.counts` | total/critical/major/minor/suggestion 及 by_category |
| `review_report.findings` | 逐条问题清单 |
| `evidence.evidence_map` | 每条 finding 的 finding_id+locator+quote+basis+source_ref |
| `sources` / `uncertainty` / `pending_human` / `limitations` | 数组，如实填写 |
| `errors` / `degradations` | 数组，记录失败与降级 |
| `change_log` | 数组，每条含 seq/stage/summary（seq 递增，确定性） |
| `runtime` | started_at/finished_at/elapsed_sec/version |
| `output_contract` 全部字段 | 若 case.json 含 `output_contract`，原样回填（缺值给 null/[]/""，不改名不删未知键） |

finding 必填键：`id`、`category`、`locator`、`quote`、`verdict`、`severity`、
`suggestion`；建议补 `finding_type`、`claim`、`basis`、`source_ref`、`confidence`、
`review_status`（`verified` \| `needs_review`）。

## 6. 提交前自检（与 scripts/selftest.py 一致）

- [ ] result.json 存在且可 `json.load`；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 七必填键齐全且非空。
- [ ] 每条结论能按 locator + quote 回查原文，quote 逐字命中抽取文本。
- [ ] verdict 与引文语义一致；severity 与问题性质匹配。
- [ ] 六类检查项与方法/结果/讨论章节均有覆盖或明确标注未覆盖。
- [ ] 冲突双侧保留；无法确认条目标 needs_review 并进 uncertainty/pending_human。
- [ ] sources 只含实际核验过的来源；未取得写 not_verified，无虚构 DOI/URL。
- [ ] status/verdict 诚实；空材料为 insufficient_evidence。
- [ ] 脚本仅标准库、`--help` 退出码 0、同一输入重复运行一致（无随机数）。
