# 审查清单与判定规则（研究方法与统计审查）

本文件是审查的判定基准。执行前通读；每条 finding 的 `facet`、`check_class`、
`finding_type`、`severity`、`evidence_grade`、`basis` 都必须取自已下合法值，
且严重度与缺陷性质匹配。红线在 §6。

## 1. 五个检查面（facet）

| facet | 逐条要问的问题 |
|---|---|
| `research_design` | 设计类型与所答研究问题是否匹配；对照/分组方式是否成立；盲法与偏倚控制是否说明；主要终点与研究假设是否事先明确；有无事后更换终点 |
| `variable_definition` | 暴露、结局、协变量、分组变量的定义/测量/编码是否一致；分类切点、单位换算、缺失值处理是否有依据；变量类型（连续/分类/计数/生存时间）与后续模型是否对得上 |
| `statistical_methods` | 检验方法与数据结构、分布假设、样本独立性是否匹配；多重比较、重复测量、交互项、敏感性分析是否被正确处理；假设不满足时是否说明非参数替代 |
| `sample_size_power` | 样本量估算依据、效应量来源、α 与把握度（power）是否报告且合理；实际入组量与估算量、失访与剔除如何处理；是否以 post-hoc 功效冒充先验功效 |
| `results_reporting` | 效应量与置信区间、精确 p 值、统计量与自由度是否报告；图表与正文数值是否一致；是否满足对应报告规范（CONSORT/STROBE/PRISMA 等） |

## 2. 六类审查面（check_class）

`fact_check`（事实核验）、`citation_support`（引用支持）、`method_design`
（方法设计）、`statistical_reporting`（统计报告）、`argumentation_chain`
（论证链）、`academic_norms`（学术规范）。

一条 finding 同时标注 facet 与 check_class：如"主要结局 p=0.06 却写显著"是
`results_reporting` + `statistical_reporting`；"配对数据却用独立 t 检验"是
`statistical_methods` + `method_design`。五个 facet 与相关 check_class 都要走查，
确无问题时在 `meta.json.checked_facets`/`checked_classes` 标注"已检查"。

## 3. 缺陷类型（finding_type）

| 值 | 含义 | 关键区别 |
|---|---|---|
| `factual_error` | 事实/数值/计算错误，或方法与数据明确不匹配 | 是"作者做错了" |
| `insufficient_evidence` | 作者未报告必要的设计/统计信息，或材料不足以判断 | 是"没报告/材料不足"，**不是**"做错了" |
| `normative_issue` | 违反报告规范、格式、伦理/利益冲突/数据可用性声明等 | 是"不符合规范" |
| `advisory` | 可接受但有改进空间，非缺陷 | 是"建议性意见" |
| `pending_review` | 无法确认（缺来源/版本冲突/需外查），标记待复核 | 不凭空补结论 |

## 4. 严重度（severity）

| 值 | 定义 | 典型例子 |
|---|---|---|
| `critical` | 使主要结论失效、或分析不可复现/不可信的硬伤 | 主要结局 p/CI/显著性表述矛盾；配对数据用独立检验且结论因此反转；样本量计算与实际入组存在算术矛盾 |
| `major` | 明显削弱证据或某项关键次要结论，必须修但可修 | 多重比较不校正；主要结局缺置信区间；缺失值处理未说明且影响结论 |
| `minor` | 应报告但不改变结论 | 缺精确 df/统计量；图表与正文非关键数值不一致；报告不规范 |
| `advisory` | 可选改进，非缺陷 | 建议补敏感性分析、建议更清晰画图 |
| `pending` | 严重度尚不能定，需先复核事实 | 来源版本冲突、外查未完成 |

原则：按缺陷对结论的实际影响分级，不一律写 critical；拿不准影响面的一律降一档，
并在 `statement` 写清"影响面待确认"。

## 5. 证据强度（evidence_grade + basis，本 Skill 的降级/不确定性核心）

每条 finding 必须自带证据强度，不能只给一句结论。`evidence_grade` 回答"结论证据
有多硬"；`basis` 回答"依据从哪来"。

| evidence_grade | 含义 |
|---|---|
| `confirmed` | quote 逐字摘自原文，且缺陷可直接由 quote 本身判定（如数值矛盾） |
| `probable` | quote 逐字，但结论需要解释/推断（如"缺校正"须结合上下文） |
| `not_verified` | 无法核对来源（缺来源/OCR 失败/版本冲突），只能存疑 |
| `not_reported` | 所需信息在材料中不存在，本身即构成"未报告"证据 |

| basis | 含义 |
|---|---|
| `verbatim` | quote 为逐字摘录 |
| `inferred` | quote 逐字，但缺陷判定是推理结果 |
| `external` | 依据外部规范（报告规范、方法学教材规则） |
| `not_verified` | 未取得可核对的来源 |

判定铁律：`evidence_grade=not_verified` 的条目必须 `finding_type=pending_review`、
`severity=pending`、`review_status=needs_review`，并写入
`meta.json.uncertainty`/`pending_human`，**不得**当作已确认结论。

## 6. 红线（严重度与性质匹配）

- 不得把"未报告"判成 `factual_error`；"未报告"是 `insufficient_evidence`。
- `insufficient_evidence` 的严重度最高为 `major`，不得写 `critical`。
- `factual_error` 的严重度只能取 `critical/major/minor`，不得写 `advisory/pending`。
- 可接受做法不得判成 `factual_error` 或 `critical`。
- 真正硬伤不得降级写成 `advisory`。
- `pending_review` 的严重度只能是 `pending`。
- `advisory`（finding_type）的严重度只能是 `advisory`。

脚本 `finalize.py` 会对以上规则做自动校验，违规项写入 result.json 的 `validation`
数组（`level=error`），但不阻塞落盘；须在收尾前修正。

## 7. 方法—数据类型匹配（判定"方法是否不当"的依据）

| 数据结构 / 结局类型 | 通常合适的方法 | 常见错配 |
|---|---|---|
| 连续结局，两组独立，正态、方差齐 | 独立样本 t 检验；方差不齐用 Welch | 配对数据误用独立 t |
| 连续结局，自身前后/配对 | 配对 t 检验、线性混合模型 | 忽略配对，破坏独立性 |
| 二分类结局 | χ²、Fisher 精确、logistic 回归 | 用 t 检验比分类结局 |
| 计数结局 | Poisson、负二项回归 | 线性回归忽略非负离散 |
| 生存时间 | Kaplan-Meier + log-rank、Cox 回归 | 用 t 检验比时间 |
| 三组及以上连续 | 单因素 ANOVA + 校正后的两两比较 | 反复 t 检验不校正 |
| 重复测量/多水平 | 混合效应模型、GEE | 把重复观测当独立样本 |
| 多个主要/次要终点 | 预声明层级 + 多重比较校正（Bonferroni/Holm/FDR） | 不校正导致 I 类错误膨胀 |
| 不满足正态 | 变换、非参数（Mann-Whitney/Wilcoxon/Kruskal） | 直接参数检验且不查假设 |

判定三档：**方法不当（错）** → `factual_error`；**方法可接受但报告不全** →
`insufficient_evidence`；**方法可接受、可改** → `advisory`。

## 8. 数值一致性检查（脚本产出候选，须人工确认）

`finalize.py` 的 `numeric_checks.candidates` 只输出**候选**，`confirmed=false`，
须逐条回原文确认后提升为 finding，未确证者不冒充结论。规则：

1. 同段内 p 值与"显著/不显著"措辞相反 → 候选。
2. 同段内 p 值与 95% CI 结论相反（比值指标 null=1，差值指标 null=0）→ 候选。
3. 全材料同一语境（total/enrolled/analyzed 或"总/纳入/分析"）下 N/n 数值不一致 → 候选。

复核动作：回 `locator` 读原句，确认 p 与 CI/措辞指向同一结局、N 是总数还是分组数；
确证后写 finding（`factual_error`），未确证不写或写 `pending_review`。

## 9. 定位与逐字摘录约定

- `locator` 用可回查串：`p.3 ¶2`（页·段）、`p.4 Table 1`、`p.6 Fig.2`；纯文本用 `¶2`。
  定位索引见 `/app/work/segments.json`，逐字原文见 `/app/work/texts/*.txt`。
- `quote` 必须逐字摘自 `segments.json`/`texts/*.txt`，不得改写、不得拼接非相邻文字。
- 冲突信息不隐藏：同一指标两处不一致时，两处 locator 都记录（finding 的 `conflicts`
  数组），`basis` 记两处来源。
- 无法确认的条目：`review_status=needs_review`、`evidence_grade=not_verified`，
  写入 `uncertainty`/`pending_human`；未取得的来源在 `basis` 写 `not_verified`，
  不虚构 DOI/URL/数值。

## 10. 完成标准（对应评分维度）

- 五个检查面、相关六类审查面全部走查或显式标注 `checked_facets`/`checked_classes`。
- 每条 finding 必填 `id/facet/check_class/finding_type/severity/locator/quote/
  statement/suggestion/basis/evidence_grade/confidence/review_status`。
- 每条结论能按 `locator + quote` 回查原文，严重度与缺陷性质匹配，证据强度如实标注。
- 数值矛盾已用脚本候选检查兜底，确证项已入 findings。
- `limitations`/`uncertainty`/`pending_human`/`degradations` 如实声明，不凭空补结论。
