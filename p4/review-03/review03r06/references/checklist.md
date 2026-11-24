# 审查清单与判定规则（研究方法与统计审查）

本文件是审查的判定基准。执行前通读；每条 finding 的 `facet`/`check_class`/
`finding_type`/`severity` 都必须取自合法值，且严重度与缺陷性质匹配。
`plan.json` 的 25 个问题即由第 1 节生成；先按问逐条走查，再写 finding。

## 1. 五个检查面（facet）与逐问清单

| facet | 逐问审查要点 |
|---|---|
| `research_design` | ①设计类型（RCT/队列/病例对照/横断面/配对/实验）是否陈述且与所答问题匹配；②对照/分组设置是否成立，随机化/分配隐藏/盲法是否说明；③主要终点与研究假设是否事先明确，有无事后更换终点；④选择/混杂/失访/测量偏倚是否识别并控制；⑤研究单位与统计单位是否一致（个体/眼/牙/巢/重复测量） |
| `variable_definition` | ①暴露、结局、协变量、分组变量的定义/测量方式/单位是否清晰；②分类变量切点/编码是否有依据；③连续变量单位换算、变换是否一致；④缺失值数量与处理方式是否报告；⑤变量类型（连续/二分类/计数/生存/有序）与后续模型是否对得上 |
| `statistical_methods` | ①检验方法与数据结构（独立/配对/多组/重复测量）是否匹配；②分布假设（正态、方差齐）是否检验，不满足时是否改用非参数/稳健方法；③多重比较是否校正（Bonferroni/Holm/FDR）且说明作用终点；④交互项、混杂调整、敏感性分析是否设置并报告；⑤生存/计数/重复测量等设计是否用相应模型（Cox/Poisson/混合效应/GEE） |
| `sample_size_power` | ①样本量估算是否报告 α、power、效应量及其来源；②实际入组/分析量与估算量是否一致，差异是否解释；③失访、剔除、缺失是否报告并处理（ITT/PP/敏感性）；④是否以 post-hoc 功效冒充先验功效；⑤亚组/交互检验的把握度限制是否说明 |
| `results_reporting` | ①效应量、置信区间、精确 p 值、统计量与自由度是否报告；②摘要/正文/表格/图注的数值与显著性表述是否一致；③显著性结论与 p/CI 是否一致（比值指标 null=1、差值指标 null=0）；④是否符合 CONSORT/STROBE/PRISMA 等报告规范；⑤图表单位/图例/误差条含义是否说明 |

## 2. 六类审查面（check_class）

`fact_check`（事实核验）、`citation_support`（引用支持）、`method_design`（方法设计）、
`statistical_reporting`（统计报告）、`argumentation_chain`（论证链）、`academic_norms`
（学术规范）。

一条 finding 同时标注 facet 与 check_class：如"主要结局 p=0.06 却写显著"是
`results_reporting` + `statistical_reporting`；"配对数据却用独立 t 检验"是
`statistical_methods` + `method_design`。五个 facet 与六个 check_class 都应尽量覆盖，
确无问题时在 `meta.json.checked_facets` 标注已检查。

## 3. 缺陷类型（finding_type）

| 值 | 含义 | 关键区别 |
|---|---|---|
| `factual_error` | 事实/数值/计算错误，或方法与数据明确不匹配 | 是"作者做错了" |
| `insufficient_evidence` | 未报告必要的设计/统计信息，或材料不足以下结论 | 是"没报告/材料不足"，**不是**"做错了" |
| `normative_issue` | 违反报告规范、格式、署名、伦理/利益冲突/数据可用性声明 | 是"不合规范" |
| `advisory` | 可接受但有改进空间，非缺陷 | 是"建议性意见" |
| `pending_review` | 无法确认（缺来源/版本冲突/需外查），标记待复核 | 不凭空补结论 |

**红线**：不得把"未报告"判成 `factual_error`；不得把可接受做法判成 `factual_error` 或
critical；不得把真正硬伤写成 `advisory`。

## 4. 严重度（severity）

| 值 | 定义 | 典型例子 |
|---|---|---|
| `critical` | 使主要结论失效、或分析不可复现/不可信的硬伤 | 主要结局 p/CI/显著性表述矛盾；配对数据用独立检验且结论因此反转；样本量计算与实际入组算术矛盾 |
| `major` | 明显削弱证据或某项关键次要结论，必须修但可修 | 多个比较不校正；主要结局缺置信区间；缺失值处理未说明且影响结论 |
| `minor` | 应报告但不改变结论 | 缺精确 df/统计量；图表与正文非关键数值不一致；报告不规范 |
| `advisory` | 可选改进，非缺陷 | 建议补敏感性分析、建议更清晰画图 |
| `pending` | 严重度尚不能定，需先复核事实 | 来源版本冲突、外查未完成 |

原则：**按缺陷对结论的实际影响分级，不一律写 critical**；拿不准影响面的一律降一档并写清
"影响面待确认"。

## 5. 方法—数据类型匹配（判定"方法是否不当"的依据）

| 数据结构/结局类型 | 通常合适的方法 | 常见错配 |
|---|---|---|
| 连续结局，两组独立，正态、方差齐 | 独立样本 t 检验；方差不齐用 Welch | 配对数据误用独立 t |
| 连续结局，自身前后/配对 | 配对 t 检验、线性混合模型 | 忽略配对，独立性被破坏 |
| 二分类结局 | χ²、Fisher 精确、logistic 回归 | 用 t 检验比分类结局 |
| 计数结局 | Poisson、负二项回归 | 用线性回归忽略非负离散 |
| 生存时间 | Kaplan-Meier + log-rank、Cox 回归 | 用 t 检验比时间 |
| 三组及以上连续 | 单因素 ANOVA + 校正后的两两比较 | 反复 t 检验不校正 |
| 重复测量/多水平 | 混合效应模型、GEE | 把重复观测当独立样本 |
| 多个主要/次要终点 | 预声明层级 + 多重比较校正 | 不校正导致 I 类错误膨胀 |
| 数据不满足正态 | 变换、非参数（Mann-Whitney/Wilcoxon/Kruskal） | 直接参数检验且不查假设 |

判定区分三层：**方法不当（错）** → `factual_error`；**方法可接受但报告不全** →
`insufficient_evidence`；**方法可接受、可改** → `advisory`。

## 6. 数值一致性检查规则（脚本实现，供复核）

`scripts/finalize.py` 的 `numeric_checks` 扫描 `segments.json`，只输出**候选**，须人工确认后
提升为 finding：

1. 同段内 p 值与"显著/不显著"措辞相反 → 候选矛盾。
2. 同段内 p 值与 95% CI 结论相反（比值指标 null=1、差值指标 null=0）→ 候选矛盾。
3. 全材料多处 `N/n = 数字` 值不一致 → 候选矛盾（可能是总样本量 vs 分组样本量）。

复核动作：回 `locator` 读原句，确认 p 与 CI/措辞指向同一结局，确认 N 是总数还是分组数；
确证后写 finding，未确证则不写（或写 `pending_review`）。

## 7. 定位与逐字摘录约定

- `locator` 用可回查串：`p.3 ¶2`（页·段）、`p.4 Table 1`、`p.6 Fig.2`，纯文本用 `¶2`。
  定位索引见 `/app/work/segments.json`。
- `quote` 必须逐字摘自 `/app/work/texts/*.txt` 或 `segments.json` 的 text，不得改写、不得
  拼接非相邻文字。
- 冲突信息不隐藏：同一指标两个位置不一致时，两条证据都记录，并在 finding 里标出两处 locator。
- 无法确认条目：`review_status=needs_review`，写入 `uncertainty`/`pending_human`；未取得的
  来源在 `basis` 写 `not_verified`，不虚构 DOI/URL/数值。

## 8. 完成标准（对应评分维度）

- 五个检查面在 `plan.json` 中逐问有状态，或显式标 `unchecked`/`checked_facets`。
- 每条 finding 六必填（`id/facet/locator/quote/severity/suggestion`）+ `finding_type`/
  `check_class`/`confidence`/`review_status`/`source_sha` 语义齐全。
- 每条结论能按 `locator + quote` 回查原文，严重度与缺陷性质匹配。
- 事实错误、证据不足、规范问题、建议性意见、待复核五类已区分。
- 数值矛盾已用脚本候选检查兜底，确证项已入 findings。
- 局限、不确定性、待复核、未检查项已如实声明，不凭空补结论。
