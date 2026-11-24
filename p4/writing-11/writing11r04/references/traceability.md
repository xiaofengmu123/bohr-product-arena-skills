# 证据溯源与术语表规范

## 溯源要求

每个关键结论、数值、术语、引用、图元都要能指回材料中的逐字片段。规范化后写入
`result.json` 的 `fact_source_map`，字段：

```json
{
  "item": "87.5%",
  "kind": "number",
  "value": "5 年生存率为 87.5%",
  "source": "/input/materials/paper.pdf#p3",
  "locator": "paper.pdf 第 3 页第 2 段",
  "quote": "The 5-year survival rate was 87.5% [12,13]",
  "note": "数字原样保留；引文 [12,13] 一并保留",
  "status": "verified"
}
```

- `locator` 用 `文件路径 + 页码/段落/行号`；quote 必须逐字引用原文，不得改写。
- `status` 取值：`verified`（可溯源且一致）、`inferred`（推断，需 `note` 标注"推断/待核"）、
  `pending_human`（需人工确认）、`not_reported`（原文未报告）、`source_conflict`（多来源冲突）。
- 冲突信息不隐藏：同一指标两处不一致时两处都记录，标 `source_conflict`，不擅自取舍。
- 不虚构引用、DOI、数值；缺失就写 `not_reported` 或 `pending_human`。

## 术语表模板（glossary.json）

```json
{
  "terms": [
    {
      "canonical": "机器学习",
      "variants": ["machine learning", "ML"],
      "discipline": "computer science",
      "context": "摘要与正文通用译法",
      "status": "locked"
    },
    {
      "canonical": "细胞",
      "variants": ["cell"],
      "discipline": "biology",
      "context": "本文指生物细胞，非电池",
      "status": "pending_human"
    }
  ]
}
```

规则：

- `canonical` 为全文统一译法；`variants` 列允许出现的同义/缩写写法；正文同时出现多个
  variant 时 `check_translation.py` 会报 `inconsistent`。
- 同词异学科必须写 `discipline` + `context` 消歧，两个候选都保留；最终选译放入
  `pending_human`，等作者确认。
- `status`：`locked`（已定译法）、`pending_human`（待人工选译）、`unsure`（学科不明确）。

## 数字核对规则

- 数字串存在性可由 `check_translation.py` 自动核对：译文含相同数字串 → 强正向信号。
- 译文数字串缺失不等于错误：可能是 `10 million → 一千万` 这类合法改写。此时进入
  manual-verify 队列，由 Agent 确认改写是否数值等价，并登记到 `fact_source_map`。
- 单位/量级换算（mM→mol/L、万/亿 vs million/billion）必须显式核对并记录换算过程。
- 有效数字位数不因翻译改变；范围与年份原样落地。

## 语向与输出形态

- 语向在 P0 从 `case.json`/`task.md` 判定（EN→ZH / ZH→EN / 双语）；无法判定时
  `pending_human` 登记"语向不确定"。
- 输出形态（纯译文 / 双语对照 / 带修订标记）按任务要求交付；双语对照稿保持句段一一对应。
- 公式、表格、题注、脚注、参考文献条目原位保留，编号不重排。
