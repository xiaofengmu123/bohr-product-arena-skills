# 可复用模板：检索式 / 纳排标准 / 质量量表

以下模板是可直接复用的资源（对应评分维度"生态贡献：资源可复用性"），
与 `references/protocol-schema.md` 的字段定义一致。所有方括号均为占位符，
运行时应替换为案例真实内容；空值宁可写 `not_reported`，不要编造。

## 1. 检索式模板（query_blocks）

每个研究问题至少拆成 2 个检索块，每块带可执行布尔式 + 意图。示例：

```json
{
  "id": "q1",
  "database": "PubMed",
  "fields": "ti,ab",
  "query": "([population_synonyms]) AND ([intervention_synonyms]) AND ([outcome_synonyms])",
  "synonyms": ["……", "……"],
  "controlled_terms": ["MeSH ……"],
  "time_range": "2015-2026",
  "language_limits": "English",
  "intent": "定位核心疗效证据，服务于子问题 sq1",
  "status": "run",
  "hits": 0,
  "run_date": "YYYY-MM-DD"
}
```

构造规则：

1. 把研究问题拆成 PICO/PICOS 要素（对象、干预/暴露、对照、结局、研究设计），
   每个要素列同义词与受控词，再用 `AND` 连接要素、`OR` 连接同义词。
2. 写明数据库、字段（`ti,ab` / 全文）、时间与语言限制。
3. `intent` 必须说清"这一段检索服务于哪个子问题、补哪块证据"。
4. 检索为空时把 `status` 改为 `not_run`，在 `hits` 留 0，并在 `degradations`
   记"数据库不可用/无命中"，禁止伪造命中数。

## 2. 纳排标准模板（条款化、可判定、带优先级）

```json
{
  "id": "in1",
  "statement": "纳入 2015-2026 年发表的随机对照试验（RCT）",
  "priority": 1,
  "applies_to": "title_abstract",
  "version": 1
}
```

写法要求：

- 每条只用一句可判定命题；避免"质量较高""相关研究"这类无法机械判定的措辞。
- `priority` 数值小者优先；两条条款对同一记录冲突时按 priority 裁决，并把裁决
  写进该记录的 `reason`。
- 常见维度：研究设计（`in_研究类型` / `ex_非目标设计`）、对象/人群、暴露/干预、
  结局、时间、语言、可得全文（`ex_仅摘要` 对应 `abstract_only` 降级）。

## 3. 质量评价量表模板（quality_rubric）

对纳入研究逐维度打分，量表维度与等级先定义再使用（放 `plan.json`）：

```json
{
  "dimensions": [
    {"id": "q_selection", "name": "选择偏倚", "definition": "随机化/分配隐藏是否充分", "scoring": "low/some/high"},
    {"id": "q_attrition", "name": "失访偏倚", "definition": "失访与结局缺失是否平衡", "scoring": "low/some/high"},
    {"id": "q_reporting", "name": "报告完整性", "definition": "主要结局与预设结局是否完整报告", "scoring": "low/some/high"},
    {"id": "q_applicability", "name": "适用性", "definition": "对象/干预/结局是否贴合本综述问题", "scoring": "low/some/high"}
  ],
  "grades": ["high", "moderate", "low", "critical"]
}
```

打分时逐维度写 `basis_locator + quote`（原文定位 + 逐字引用），再给
`overall_grade`，并写明 `impact_on_conclusion`：

- `high` → 结论按 `supported` 表述；
- `moderate` → 结论降为 `suggestive`；
- `low` / `critical` → 结论标 `not_reported` 或只作背景，不进入主要综合结论。

不允许只写一句"质量较高"；没有定位与引用的维度判定视为未完成（`selfcheck` 告警）。

## 4. 结论强度词表（防止越界）

| 证据状态 | 结论措辞强度 |
|---|---|
| 多来源一致、低偏倚 | `supported`（证据支持） |
| 单一/中等质量、间接 | `suggestive`（初步提示） |
| 仅摘要/无全文/不可得 | `not_reported`（未报告，不下结论） |
| 来源互相矛盾 | 并列记录并标 `undetermined`，不取一方 |

区分三类陈述：`measured`（材料中报告的观测）、`inferred`（合理推断）、
`assumed`（假设）；结论里不得把 `inferred`/`assumed` 写成 `measured`。
