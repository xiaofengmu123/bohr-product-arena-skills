# 审查清单与判定规则（研究方法与统计审查）

执行前通读本文件。每条 finding 的 `facet`/`check_class`/`finding_type`/`severity`
只能取下列合法值；严重度必须与缺陷性质匹配，不得一律写 critical，也不得把硬伤
写成 advisory。

## 1. 五个检查面（facet）

| facet | 逐条要问的问题 |
|---|---|
| `research_design` | 设计类型是否匹配所答问题；对照/分组/盲法与偏倚控制是否说明；主要终点与假设是否事先明确；有无事后更换终点 |
| `variable_definition` | 暴露、结局、协变量、分组变量的定义/测量/编码是否一致；分类切点、单位换算、缺失值处理是否有依据；变量类型与统计模型是否对得上 |
| `statistical_methods` | 检验方法与数据结构、分布假设、样本独立性是否匹配；多重比较、重复测量、交互项、敏感性分析是否处理正确；假设不满足时是否给非参数替代 |
| `sample_size_power` | 样本量估算依据、效应量来源、α 与把握度是否报告且合理；实际入组量与估算量、失访与剔除如何处理；是否把事后功效冒充先验功效 |
| `results_reporting` | 效应量与置信区间、精确 p 值、统计量与自由度是否报告；图表与正文数值是否一致；是否满足 CONSORT/STROBE/PRISMA 等规范 |

## 2. 六类审查面（check_class）

`fact_check`（事实/数值核验）、`citation_support`（引用支持）、`method_design`
（方法设计）、`statistical_reporting`（统计报告）、`argumentation_chain`
（论证链）、`academic_norms`（学术规范）。一条 finding 同时标注 facet 与
check_class，例如"主要结局 p=0.06 却写显著"是 `results_reporting` +
`statistical_reporting`；"配对数据却用独立 t 检验"是 `statistical_methods` +
`method_design`。

## 3. 缺陷类型（finding_type）

| 值 | 含义 | 关键区分 |
|---|---|---|
| `factual_error` | 事实/数值/计算错误，或方法与数据明确不匹配 | 是"作者做错了" |
| `insufficient_evidence` | 作者未报告必要信息，或材料不足以下结论 | 是"作者没报告/材料不足"，**不是**做错 |
| `normative_issue` | 违反报告规范、伦理/利益冲突/数据可用性声明等 | 是不符合规范 |
| `advisory` | 可接受但有改进空间，非缺陷 | 是建议 |
| `pending_review` | 无法确认（缺来源/版本冲突/需外查） | 不凭空补结论 |

红线：不得把"未报告"判成 `factual_error`；不得把可接受做法判成
`factual_error` 或 critical；不得把真正硬伤写成 `advisory`。

## 4. 严重度（severity）

| 值 | 定义 | 典型例子 |
|---|---|---|
| `critical` | 使主要结论失效或分析不可复现 | 主要结局 p/CI 与措辞矛盾；配对数据误用独立检验且结论因此反转；样本量算术矛盾 |
| `major` | 明显削弱证据或某关键次要结论，必须修但可修 | 多重比较不校正；主要结局缺置信区间；缺失值处理未说明且影响结论 |
| `minor` | 应报告但不改变结论 | 缺精确 df/统计量；非关键数值正文图表不一致；报告不规范 |
| `advisory` | 可选改进，非缺陷 | 建议补敏感性分析、更清晰画图 |
| `pending` | 严重度尚不能定，需先复核 | 来源版本冲突、外查未完成 |

原则：按缺陷对结论的**实际影响**分级；拿不准影响面一律降一档并注明"影响面待确认"。

## 5. 方法—数据类型匹配（判断"方法是否不当"）

| 数据结构 / 结局 | 通常合适的方法 | 常见错配 |
|---|---|---|
| 两组独立连续、正态、方差齐 | 独立样本 t；方差不齐用 Welch | 配对数据误用独立 t |
| 自身前后/配对 | 配对 t、线性混合模型 | 忽略配对破坏独立性 |
| 二分类结局 | χ²、Fisher 精确、logistic 回归 | 用 t 检验比分类结局 |
| 计数结局 | Poisson、负二项回归 | 线性回归忽略非负离散 |
| 生存时间 | Kaplan-Meier + log-rank、Cox | 用 t 检验比时间 |
| 三组及以上连续 | ANOVA + 校正两两比较 | 反复 t 检验不校正 |
| 重复测量/多水平 | 混合效应、GEE | 把重复观测当独立样本 |
| 多个终点 | 预声明层级 + 多重比较校正 | 不校正致 I 类错误膨胀 |
| 不满足正态 | 变换、非参数（MWW/Wilcoxon/Kruskal） | 直接参数检验且不查假设 |

三层区分：**方法不当（错）** → `factual_error`；**方法可接受但报告不全** →
`insufficient_evidence`；**方法可接受、可改** → `advisory`。

## 6. 数值一致性检查（脚本生成候选，须人工确证）

`scripts/main.py finalize` 会扫描 `segments.json`，只输出候选（不冒充结论）：

1. 同段 p 值与"显著/不显著"措辞相反。
2. 同段 p 值与 95% CI 结论相反（比值指标 null=1，差值指标 null=0）。
3. 全材料出现多个不一致的 `N/n = 数字` 值（可能是总样本 vs 分组样本）。

复核动作：回读 `locator` 原句，确认 p/CI/措辞指向同一结局，确认 N 是总数还是
分组数；确证后提升为 finding，未确证则不写或写 `pending_review`。

## 7. 定位与逐字摘录约定

- `locator` 用可回查串：`p.3 ¶2`（页·段）、`p.4 Table 1`、`p.6 Fig.2`；纯文本用
  `¶2` 并加文件名。索引见 `/app/work/segments.json`，全文见 `/app/work/texts/*.txt`。
- `quote` 必须逐字摘自脚本抽出的文本，不得改写、不得拼接非相邻文字。
- 同一指标两处不一致时两条证据都记录，并在 finding 中给出两处 locator。
- 无法确认的条目：`review_status=needs_review`，写入 `uncertainty`/`pending_human`；
  未取得的来源在 `basis` 写 `not_verified`，不虚构 DOI/URL/数值。

## 8. 完成标准（对应评分维度）

- 五个检查面（研究设计—变量定义—统计方法—样本量—结果报告）全部走查，或显式在
  `meta.json.checked_facets` 标注 unchecked。
- 六类审查面（事实核验—引用支持—方法设计—统计报告—论证链—学术规范）覆盖或显式
  标注 unchecked。
- 每条 finding 六必填齐全 + `finding_type`/`check_class`/`confidence`/
  `review_status`/`basis` 语义齐全。
- 每条结论可按 `locator + quote` 回查原文；严重度与缺陷性质匹配；五类缺陷已区分。
- 数值矛盾已由脚本候选检查兜底，确证项入 findings，其余不冒充结论。
- 局限、不确定性、待复核、未检查项如实声明，不凭空补结论。
