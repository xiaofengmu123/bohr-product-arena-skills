# 审查清单与判定规则（研究方法与统计审查）

本文件是判定基准。执行前通读；每条 finding 的 `facet`、`check_class`、
`finding_type`、`severity` 必须取自下列合法值，且严重度与缺陷性质匹配。
最终落盘顺序由 `scripts/main.py finalize` 统一按「检查面 → 定位串 → id」排序，
并按 `F001…` 重新编号，因此 Agent 写 finding 时只需保证六必填与枚举合法。

## 1. 五个检查面（facet）

| facet | 审查要点（逐条问） |
|---|---|
| `research_design` | 设计类型与研究问题是否匹配；对照/分组是否成立；盲法与偏倚控制是否说明；主要终点与假设是否事先明确；有无事后更换终点 |
| `variable_definition` | 暴露、结局、协变量、分组变量的定义/测量/编码是否一致；分类切点、单位换算、缺失值处理是否有依据；变量类型（连续/分类/计数/生存）与统计模型是否对得上 |
| `statistical_methods` | 检验方法与数据结构、分布假设、样本独立性是否匹配；多重比较、重复测量、交互、敏感性分析是否处理；假设不满足时是否给出非参数替代 |
| `sample_size_power` | 样本量估算依据、效应量来源、α 与把握度是否报告且合理；实际入组与估算量、失访/剔除如何处理；是否以 post-hoc 功效冒充先验功效 |
| `results_reporting` | 效应量与置信区间、精确 p 值、统计量与自由度是否报告；图/表/正文数值是否一致；是否满足 CONSORT/STROBE/PRISMA 等规范 |

## 2. 六类审查面（check_class）

`fact_check`（事实核验）、`citation_support`（引用支持）、`method_design`
（方法设计）、`statistical_reporting`（统计报告）、`argumentation_chain`
（论证链）、`academic_norms`（学术规范）。

一条 finding 同时标注 facet 与 check_class：例如「主要结局 p=0.06 却写显著」是
`results_reporting` + `statistical_reporting`；「配对数据却用独立 t 检验」是
`statistical_methods` + `method_design`。五个 facet 必须全部走查或显式标注
`unchecked`；六个 check_class 按实际证据覆盖，不为凑数而硬贴标签。

## 3. 缺陷类型（finding_type）

| 值 | 含义 | 关键区别 |
|---|---|---|
| `factual_error` | 事实/数值/计算错误，或方法与数据明确不匹配 | 是「作者做错了」 |
| `insufficient_evidence` | 未报告必要设计/统计信息，或材料不足以判断 | 是「没报告/材料不足」，**不是**「做错了」 |
| `normative_issue` | 违反报告规范、格式、伦理/利益冲突/数据可用性声明 | 是「不符合规范」 |
| `advisory` | 可接受但有改进空间，非缺陷 | 是「建议」 |
| `pending_review` | 无法确认（缺来源/版本冲突/需外查） | 不凭空补结论 |

红线：不得把「未报告」判成 `factual_error`；不得把可接受做法判成
`factual_error` 或 `critical`；不得把硬伤写成 `advisory`。

## 4. 严重度（severity）

| 值 | 定义 | 典型例子 |
|---|---|---|
| `critical` | 使主要结论失效或分析不可复现的硬伤 | 主要结局 p/CI/显著性表述矛盾；配对数据误用独立检验且结论因此反转；样本量算术矛盾 |
| `major` | 明显削弱证据或关键次要结论，必须修但可修 | 多重比较不校正；主要结局缺置信区间；缺失处理未说明且影响结论 |
| `minor` | 应报告但不改变结论 | 缺精确 df/统计量；图正文非关键数值不一致 |
| `advisory` | 可选改进，非缺陷 | 建议补敏感性分析、建议更清晰画图 |
| `pending` | 严重度尚不能定，需先复核事实 | 来源版本冲突、外查未完成 |

原则：按缺陷对结论的实际影响分级；拿不准影响面一律降一档并写明「影响面待确认」。

## 5. 方法—数据类型匹配（判定「方法是否不当」的依据）

| 数据结构 / 结局 | 通常合适 | 常见错配 |
|---|---|---|
| 连续，两组独立，正态方差齐 | 独立 t；方差不齐 Welch | 配对数据误用独立 t |
| 自身前后/配对 | 配对 t、线性混合模型 | 忽略配对破坏独立性 |
| 二分类 | χ²、Fisher、logistic | 用 t 检验比分类结局 |
| 计数 | Poisson、负二项 | 线性回归忽略非负离散 |
| 生存时间 | Kaplan-Meier + log-rank、Cox | 用 t 检验比时间 |
| 三组及以上连续 | ANOVA + 校正两两比较 | 反复 t 不校正 |
| 重复测量/多水平 | 混合效应、GEE | 把重复观测当独立 |
| 多个终点 | 预声明层级 + Bonferroni/Holm/FDR | 不校正致 I 类错误膨胀 |
| 不满足正态 | 变换、Mann-Whitney/Wilcoxon/Kruskal | 直接参数检验不查假设 |

三层判定：方法不当 → `factual_error`；方法可接受但报告不全 →
`insufficient_evidence`；方法可接受、可改 → `advisory`。

## 6. 数值一致性检查规则（脚本自动候选，须人工确认）

`scripts/main.py finalize` 的 `numeric_checks` 扫描 `segments.json`，只输出候选：

1. `p_vs_wording`：同段 p 值与「显著/不显著」措辞相反 → 候选矛盾。
2. `p_vs_ci`：同段 p 值与 95% CI 结论相反（比值指标 null=1，差值指标 null=0）→ 候选矛盾。
3. `n_inconsistency`：全材料多处 `N/n = 数字` 值不一致 → 候选（可能总数 vs 分组）。

复核动作：按 `locator` 回读原句，确认 p 与 CI/措辞指向同一结局、N 是总数还是
分组数；确证后写 finding，未确证不写或写 `pending_review`。候选绝不冒充结论。

## 7. 定位与逐字摘录约定

- `locator` 用可回查串：PDF 用 `p.3 ¶2`（页·段）；纯文本用 `materials/xx.txt ¶6`。
  索引见 `/app/work/segments.json`。
- `quote` 必须逐字摘自 `/app/work/texts/` 或 `segments.json` 的原文，不改写、
  不拼接非相邻文字；脚本会自动核验 quote 是否能在原文定位到，找不到则标
  `needs_review` 并记入 `degradations`。
- 冲突信息不隐藏：同一指标两处不一致时两条证据都记录，并在 finding 里标出两处
  locator；`review_status=source_conflict`。
- 无法确认的条目标 `needs_review` 并写入 `uncertainty`/`pending_human`；未取得的
  来源在 `basis` 写 `not_verified`，不虚构 DOI/URL/数值。

## 8. 确定性约定（本版重点）

- 脚本层确定性：同一输入 + 同一 `findings/*.json` 集合，`finalize` 两次运行产出
  **字节一致** 的 `result.json` 与 `report.md`；时间戳只进 `state.json`，不进交付物。
- 判断层降噪：按固定 facet 顺序走查；quote 逐字不意译；严重度按 §4 匹配，同一类
  证据给出同一严重度；不因案例领域标签改变判定。
- finding id 由脚本在最终排序后统一重编号为 `F001…`，因此回读最终 id 以
  `result.json` 为准。

## 9. 完成标准

- 五个 facet 全部走查或显式标 `unchecked`。
- 每条 finding 六必填（`id/facet/locator/quote/severity/suggestion`）+ 显式
  `finding_type`/`check_class`/`confidence`/`review_status`。
- 每条结论可按 `locator + quote` 回查原文；严重度与性质匹配。
- 五类缺陷已区分；数值矛盾已用脚本候选兜底并人工复核。
- 局限、不确定性、待复核、未检查项如实声明；不编造。
