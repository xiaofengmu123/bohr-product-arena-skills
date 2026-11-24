# 六类分面检查清单与误报抑制

本文件是 Agent 逐条排查的依据。**每条 finding 必须能指回原文（locator + 逐字 quote）**；
拿不到逐字引用的，不得标 `confirmed`。

## 1. fact_check 事实核验

检查锚点：
- 摘要/正文/图表/结论中的数值、日期、专名、定义是否一致（冲突记 `source_conflict`，两处都录，不擅自取舍）。
- 术语与定义前后是否统一；首字母缩写是否在首次出现处定义后使用。
- 与学科常识明显冲突的陈述（单位、数量级、公认事实、已知常数）。

严重度判据：
- `critical`：核心结论依赖的错误事实。
- `major`：影响部分结论/方法的事实不一致。
- `minor`：不影响结论的笔误/术语不一致。
- `suggestion`：可改进但不构成错误。

## 2. citation_support 引用支持

检查锚点：
- 正文论断与其后所引文献的**主张**是否对应（不是"文献存在"就算支持）。
- 被引文献的主张与本文论断逐条对照：列出"本文论断 → 被引主张"，错配/过度引用单列。
- 引文编号、作者、年份是否与参考文献表一致。

无法核验（参考文献缺失/无全文）：列 `unchecked_items` + `uncertainty`，注明"无法核验"，
**不臆断被引文献主张**。

**证据映射写法（可核验性的关键）**：citation_support 的 finding，其 `claim` 写本文论断，
`evidence[]` 写被引文献的可定位片段：

```json
{
  "claim": "本文称“既往研究证实 X 可使 Y 降低 30%”[12]",
  "evidence": [
    {"source_id": "D1", "locator": "p4#para2",
     "quote": "本研究结果显示 X 组 Y 值下降 30%……"},
    {"source_id": "D2", "locator": "p9#para1",
     "quote": "在多数队列中 X 与 Y 的关联方向不一致"}
  ]
}
```

判定：若被引文献的 quote 与本文论断方向一致 → 支持成立；若方向相反/仅部分支持 →
`citation_support` + `factual_error`（错配）或 `insufficient_evidence`（过度引用）。

## 3. method_design 方法设计

检查锚点：
- 对照设置、样本量与抽样方式、纳入/排除标准、评价指标选择。
- 重复、随机化/盲法、实验条件控制、批间变异控制。
- 区分"方法本身有缺陷"（`factual_error`）与"方法可接受但报告不全"（`insufficient_evidence`）。

## 4. statistics_report 统计报告

检查锚点：
- 显著性报告是否含检验统计量、自由度、效应量、置信区间、误差范围。
- 多重比较是否校正；p 值是否给具体值而非只写"<0.05"。
- 样本量与统计方法是否匹配；缺失数据处理是否说明。

常见缺失（`insufficient_evidence`）：只报 p 值、无 CI/效应量、无校正说明、n 不明。

## 5. argument_chain 论证链

检查锚点：
- 从假设到结论的推理路径是否连续；定位缺失环节、循环论证。
- 相关 vs 因果是否混用；结论外推是否超出数据范围。
- 摘要/结论是否回应引言提出的问题。

## 6. academic_norms 学术规范

检查锚点：
- 数据可用性、代码可用性声明；伦理声明；利益冲突声明；资助来源。
- 图表版权与来源标注；术语与格式是否符合目标期刊。
- 把规范问题（`standards_issue`）与实质问题（`factual_error`）分开呈现。

## 严重度 / 问题性质总则

| 严重度 | 含义 |
|---|---|
| critical | 阻断发表/结论不可信 |
| major | 需修改后才能接受 |
| minor | 局部修正 |
| suggestion | 建议性意见 |

| issue_type | 含义 |
|---|---|
| factual_error | 事实/数值/引用错配等错误 |
| insufficient_evidence | 报告不全/证据不足/无法核验 |
| standards_issue | 学术规范/格式问题 |
| suggestion | 建议性意见 |

`verdict`：`confirmed`（有逐字证据且判断成立）/ `to_review`（不确定，待复核）/
`not_checked`（未检查）。**不确定一律 `to_review`，不凭空下结论。**

## draft.json 书写约定

- `finding_id` 从 `F01` 递增，稳定、可引用。
- 每条先写 `facet`、`severity`、`issue_type`、`verdict`、`claim`、`location`，
  再从 `show` 输出逐字抄录 `quote`（可跨行、不跨段），最后写 `rationale` 与
  `revision_suggestion`。
- `evidence[]` 可选；citation_support 建议填写，method_design/statistics_report 用
  邻接段落或图表来源做证据。
- `status` 放 `ok` 当六类相关项都已处理，否则 `partial`；材料不可用放 `insufficient_evidence`。

## 逐字引用抄录规范（减少误报与核验失败）

- `quote` 必须与 `show` 输出**逐字一致**：先 `python3 scripts/main.py show --source D1`，
  从带 `[locator]` 的行整段复制，不要凭记忆改写。
- 跨行引文可保留为单个 `quote`（段落内换行在核验时按空白折叠），但**不得跨段拼接**。
- 引文内保留原文标点、数字与大小写；不要省略中间内容（用 `……` 会破坏逐字命中）。
- 无法逐字命中的 quote：先回 `show` 重抄；仍不命中则说明原文可能被 PDF 抽取改写，
  改用平台 PDF 文本工具重抽后 `scan --rescan`。
- `location` 写成 quote 实际所在段落（与 `show` 的 locator 一致）；`verify` 会在
  location 与 quote 命中位置不一致时给出 WARN，应据实修正后再 build。

## 误报抑制（重要）

以下情况**不**记为缺陷：
- 学科写作惯例、期刊格式差异、作者的有意取舍（若无实质影响）。
- 语言风格/措辞偏好（本 Skill 不做润色、翻译、查重）。
- 无全文可核验时的"疑似错配"（降为 `to_review` + `uncertainty`，不判 confirmed）。
- 单一观察不能确认的数值冲突（记 `source_conflict`，不判对错）。

只有同时满足"有原文定位 + 有逐字引用 + 判断与学科常识/规范一致"才可标 `confirmed`；
否则降级 `to_review`。宁可列为 `unchecked_items`，也不编造证据。
