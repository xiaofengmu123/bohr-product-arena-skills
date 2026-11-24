# 审查清单与判定规则（研究方法与统计审查）

本文件是审查的判定基准。每条 finding 的 `facet`、`check_class`、`finding_type`、
`severity` 都必须取自已下合法值，且严重度与缺陷性质匹配。合法值以
`scripts/core.py` 中的常量集合为唯一事实来源；本文只作解释，不改动取值。

## 1. 五个检查面（facet）

| facet | 逐条问 |
|---|---|
| `research_design` | 设计类型与研究问题是否匹配；对照/分组是否成立；盲法与偏倚控制是否说明；主要终点与假设是否事先明确；有无事后更换终点 |
| `variable_definition` | 暴露、结局、协变量、分组变量的定义/测量/编码是否一致；分类切点、单位换算、缺失值处理有无依据；变量类型与后续模型是否对得上 |
| `statistical_methods` | 检验方法与数据结构、分布假设、样本独立性是否匹配；多重比较、重复测量、交互项、敏感性分析是否处理；假设不满足时是否说明非参数替代 |
| `sample_size_power` | 样本量估算依据、效应量来源、α 与把握度是否报告且合理；实际入组量与估算量、失访/剔除如何处理；是否用 post-hoc 功效冒充先验功效 |
| `results_reporting` | 效应量与置信区间、精确 p 值、统计量与自由度是否报告；图表与正文数值是否一致；是否满足 CONSORT/STROBE/PRISMA 等报告规范 |

## 2. 六类核对面（check_class）

`fact_check`（事实核验）、`citation_support`（引用支持）、`method_design`（方法设计）、
`statistical_reporting`（统计报告）、`argumentation_chain`（论证链）、`academic_norms`
（学术规范）。一条 finding 同时标注 facet 与 check_class，例如：

- 主要结局 p=0.06 却写“显著”→ `results_reporting` + `statistical_reporting`；
- 配对数据误用独立 t 检验 → `statistical_methods` + `method_design`；
- 引用文献支持相反结论 → `citation_support` + `fact_check`。

五个 facet 与相关 check_class 都要覆盖；确无问题时在 `meta.json.checked_facets` 标注已检查。

## 3. 缺陷类型（finding_type）

| 值 | 含义 | 关键区别 |
|---|---|---|
| `factual_error` | 事实/数值/计算错误，或方法与数据明确不匹配 | 是“作者做错了” |
| `insufficient_evidence` | 未报告必要设计/统计信息，或材料不足以下结论 | 是“没报告/材料不足”，不是“做错” |
| `normative_issue` | 违反报告规范、格式、署名、伦理/利益冲突/数据可用性声明等 | 是“不合规范” |
| `advisory` | 可接受但有改进空间，非缺陷 | 是“建议性意见” |
| `pending_review` | 无法确认（缺来源/版本冲突/需外查） | 不凭空补结论 |

**红线**：不得把“未报告”判成 `factual_error`；不得把可接受做法判成 `factual_error`
或 `critical`；不得把真正硬伤写成 `advisory`。

## 4. 严重度（severity）

| 值 | 定义 | 典型例子 |
|---|---|---|
| `critical` | 使主要结论失效，或分析不可复现/不可信 | 主要结局 p/CI/显著性表述矛盾；配对数据用独立检验且结论因此反转；样本量计算与实际入组存在算术矛盾 |
| `major` | 明显削弱证据或某项关键次要结论，必须修但可修 | 多重比较不校正；主要结局缺置信区间；缺失值处理未说明且影响结论 |
| `minor` | 应报告但不改变结论 | 缺精确 df/统计量；图表与正文非关键数值不一致；报告不规范 |
| `advisory` | 可选改进，非缺陷 | 建议补敏感性分析、建议更清晰画图 |
| `pending` | 严重度尚不能定，需先复核事实 | 来源版本冲突、外查未完成 |

原则：**按缺陷对结论的实际影响分级，不一律写 critical**；拿不准影响面的一律降一档
并在 `basis` 写“影响面待确认”。

## 5. 方法—数据类型匹配（判定“方法是否不当”）

| 数据结构 / 结局 | 通常合适的方法 | 常见错配 |
|---|---|---|
| 连续结局，两组独立，正态、方差齐 | 独立 t 检验；方差不齐用 Welch | 配对数据误用独立 t |
| 连续结局，自身前后/配对 | 配对 t、线性混合模型 | 忽略配对破坏独立性 |
| 二分类结局 | χ²、Fisher 精确、logistic 回归 | 用 t 检验比分类结局 |
| 计数结局 | Poisson、负二项回归 | 用线性回归忽略非负离散 |
| 生存时间 | Kaplan-Meier + log-rank、Cox 回归 | 用 t 检验比时间 |
| 三组及以上连续 | 单因素 ANOVA + 校正后的两两比较 | 反复 t 检验不校正 |
| 重复测量/多水平 | 混合效应模型、GEE | 把重复观测当独立样本 |
| 多终点 | 预声明层级 + 多重比较校正（Bonferroni/Holm/FDR） | 不校正致 I 类错误膨胀 |
| 不满足正态 | 变换、非参数（Mann-Whitney/Wilcoxon/Kruskal） | 直接参数检验且不查假设 |

三层判定：**方法不当（错）** → `factual_error`；**方法可接受但报告不全** →
`insufficient_evidence`；**方法可接受、可改** → `advisory`。

## 6. 数值一致性候选（脚本自动扫描，须人工确认）

`scripts/core.py` 的 `probe` 按以下规则在 `segments.json` 上扫描，只产出
`numeric_checks` 候选，写入 `/app/work/ledger.json`：

1. 同段内 p 值与“显著/不显著”措辞相反 → 候选矛盾；
2. 同段内 p 值与 95% CI 并存 → 需确认是否指向同一结局；
3. 全材料多处 `N/n = 数字` 值不一致 → 候选矛盾（可能是总样本量 vs 分组样本量）。

复核动作：回到 `locator` 读原句，确认 p 与 CI/措辞指向同一结局，确认 N 是总数还是
分组数；确证后写 finding，未确证则不写，或写 `pending_review`。复核结论通过
`/app/work/numeric_decisions.json` 回填（`promote|discard|pending`）。

## 7. 定位与逐字摘录约定

- `locator` 用可回查串：PDF 用 `p.3 ¶2`（页·段），纯文本用 `¶2`，表格/图可写
  `p.4 Table 1`、`p.6 Fig.2`。定位索引见 `/app/work/segments.json`。
- `quote` 必须逐字摘自 `/app/work/texts/*.txt`，不得改写，不得拼接非相邻文字。
- 冲突不隐藏：同一指标两个位置不一致时，两条证据都记录，并在 finding 里标出两处 locator。
- 无法确认的条目：`review_status=needs_review`，写入 `uncertainty`/`pending_human`；
  未取得的来源在 `basis` 写 `not_verified`，不虚构 DOI/URL/数值。

## 8. 完成标准（对应评分维度）

- 五个检查面全部走查，或显式标注 `checked_facets`/`unchecked_facets`。
- 每条 finding 六必填（`id/facet/locator/quote/severity/suggestion`）+
  `finding_type`/`check_class`/`confidence`/`review_status`/`basis` 语义齐全。
- 每条结论能按 `locator + quote` 回查原文，严重度与缺陷性质匹配。
- 事实错误、证据不足、规范问题、建议性意见、待复核五类已区分。
- 数值候选已逐条复核，确证项已入 findings，其余不冒充结论。
- 局限、不确定性、待复核、未检查项已如实声明，不凭空补结论。
