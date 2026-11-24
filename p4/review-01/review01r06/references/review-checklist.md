# 六类分面审查清单与判据

本文件是 SKILL.md 第 2 步分面审查的判据来源，也是 `scripts/main.py check` 校验
severity / issue_type 枚举与 facet 覆盖的语义依据。审查时先读原文，再逐面排查；
**没有原文定位与逐字 quote 的条目不写 finding**。

## 0. 通用原则

- 每条 finding 回答四个问题：哪里（location/quote）、什么（claim）、为什么
  （rationale/evidence）、怎么改（revision_suggestion）。
- 只判“材料里能证明”的问题；拿不准 → `verdict=to_review`，材料缺失 →
  `not_checked` 或 `insufficient_evidence`。
- 一个段落可产生多条 finding，但每条只讲一个独立问题，避免把多个缺陷合并成一条。
- 数值冲突（摘要/正文/图表不一致）两处都记录，标 `uncertainty.type=source_conflict`，
  不擅自取舍。

## 1. 六类 facet 的检查锚点

### 1.1 fact_check（事实核验）
- 摘要、正文、图表、结论中的数值是否一致（同一指标不得出现两套数）。
- 术语、缩写、单位、符号首次定义后前后是否统一。
- 对学科常识/可查证外部事实明显违背处（不臆断领域结论，只标可核验项）。
- 明确“本文宣称的事实”与“可核验来源”之间的差异；无来源时不判 factual_error。

### 1.2 citation_support（引用支持）
- 正文论断是否挂了对应引用；引用位置是否与论断对得上。
- 论断强度与被引文献的可支持强度是否匹配（例如“证实” vs “初步表明”）。
- 被引文献主张能否核验：能核验则写出 `cited_source_claim`；核验不到只标
  `not_checked`，**不臆断被引文献说了什么**。
- 引用列表与正文引用是否一一对应（正文引用未列入参考文献、或反向缺失）。

### 1.3 method_design（方法设计）
- 对照设置（有无对照、对照组是否合理、是否缺少基线）。
- 样本量与抽样方式（n、入排标准、代表性）。
- 评价指标选择是否与研究问题匹配。
- 重复、随机化、盲法、批间/仪器质控说明。
- 区分：**方法本身有缺陷**（issue_type=factual_error / insufficient_evidence 视证据）
  与**方法可接受但报告不全**（issue_type=standards_issue 或 suggestion）。

### 1.4 statistics_report（统计报告）
- 显著性报告：p 值、检验方法、多重比较校正。
- 误差与置信区间：是否报告误差（SD/SEM）、CI、效应量。
- 样本描述统计与检验前提是否说明。
- 图/表与正文的统计量是否一致。
- 只报 p 值不报效应量/方向 → suggestion 或 standards_issue，不判为数据造假。

### 1.5 argument_chain（论证链）
- 从假设到结论的推理路径：每步是否有证据支撑。
- 循环论证、因果与相关混淆、稻草人、结论外推超出数据范围。
- 引言提出的问题是否在结论得到回应（结构-论证一致性）。
- 定位“证据支持到 A，结论却写成 B”的断点位置。

### 1.6 academic_norms（学术规范）
- 数据可用性声明、代码/材料共享声明。
- 伦理（IRB/伦理委员会）与利益冲突声明。
- 图表版权与来源、作者贡献、致谢。
- 术语与格式是否符合目标期刊要求（不把期刊格式差异当实质缺陷）。
- 把规范问题与实质问题分开呈现。

## 2. severity 判据

| severity | 判据 |
|---|---|
| critical | 结论错误/数据矛盾/引用错配直接动摇主要结论，或学术不端迹象 |
| major | 影响可复现性、结论可信度或可发表性的实质缺陷（缺对照、缺样本量、缺显著性） |
| minor | 局部表述、术语不一致、报告不全，不影响主结论方向 |
| suggestion | 改善性建议，非缺陷（可读性、补充效应量等） |

## 3. issue_type 判据

| issue_type | 判据 |
|---|---|
| factual_error | 可证实的错误（数值矛盾、术语误用、与可核验来源冲突） |
| insufficient_evidence | 论断缺少材料内证据或必要信息（缺对照、缺样本量、无引用） |
| standards_issue | 违背报告规范/期刊要求/学科惯例但非事实错误 |
| suggestion | 建议性意见，不是错误也不是规范硬伤 |

`verdict` 与 `confidence`：
- `confirmed`：quote 直接支持该问题的成立；confidence 通常 high。
- `to_review`：证据存在但解释有歧义、或需作者确认；confidence medium/low。
- `not_checked`：材料不足以判断（如缺参考文献原文）；confidence low。

## 4. 误报抑制规则（false-positive guardrails）

1. 学科写作惯例、期刊排版差异、作者的有意取舍 → 不判缺陷（至多 suggestion 且注明）。
2. 语言润色、翻译、查重、排版等事务性问题 → 不在审查范围，不写 finding。
3. 无法逐字定位到原文的论断 → 不写 finding，放入 `unchecked_items` 或 `uncertainty`。
4. 被引文献全文不可得 → 只标 `citation_support=not_checked`，不臆断文献主张。
5. 同一问题只报一次，避免同一缺陷按 facet 重复计数。
6. 图片/图表无 OCR 或读不出数值 → 不猜数值，写 `pending_human` + degradation。
7. 主观判断类问题（如“论证不充分”）必须给具体断点与可接受范围，否则降为 suggestion。

## 5. 覆盖完成标准

- 六类 facet 都要处理：有 ≥1 条 finding，或在 `unchecked_items` 写明“不适用 / 材料
  缺失待复核”的原因。
- `check` 会把 applicable 但既无 finding 又无 unchecked 说明的 facet 列为
  `coverage_gaps`，必须补齐后才 build。
