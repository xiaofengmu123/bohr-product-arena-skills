# 证据强度分级与论断—来源记录规范

每条进入 `fact_source_map` 的实质论断（数字、术语、引用、图表、关键结论、格式改动依据）都必须能被独立回查：`claim_id → claim_text → quote（逐字）→ locator（材料定位）→ source_ref → evidence_level → revision`。缺 quote 或缺 locator 的论断不视为有证据，应移入 `pending_human` 或降级。

## 证据强度分级（evidence_level）

| 级别 | 含义 | 正文可用表述 |
|---|---|---|
| `original_measured` | 材料中直接给出的原始测量/实测值 | 事实陈述（“表明”） |
| `original_stated` | 材料中作者直接陈述的结论 | 事实陈述（“表明”） |
| `inferred` | 从材料多处的可回溯推理 | “据此推断/初步表明” |
| `conflict` | 多来源相互矛盾 | “存在争议/尚不明确”，并列出双方 |
| `not_reported` | 材料未提供 | 移入 `material_gaps`，正文不写 |

表述强度与证据级别相称：相关≠因果、单篇小样本≠领域共识、推断≠证实。冲突证据必须并列，不隐藏。

## locator 与 quote 规则

- locator 格式：文件相对路径 + 段落/章节/页码/样式名，如 `materials/paper.docx#word/document.xml:p3`、`materials/main.tex#第2节`、`materials/guide.pdf#p5`。docx 定位用 XML 路径或样式名（可被 core_impl 盘点复现）。
- quote 必须是材料原文的逐字片段（含标点），可被 `main.py --verify-only` 在材料全文中命中；命中失败即 `quote_not_locatable`，需修正 locator 或移入 `limitations`。
- 数字、DOI、参考文献条目、图表编号若为引用，quote 必须覆盖该值的上下文，不得仅写“见原文”。

## 论断—来源记录结构

```json
{
  "claim_id": "c001",
  "claim_text": "正文默认字号为小四(12pt)",
  "quote": "w:sz w:val=\"24\"",
  "locator": "materials/template.docx#word/styles.xml:docDefaults",
  "source_ref": "s1",
  "evidence_level": "original_measured",
  "revision": "按此值绑定正文样式"
}
```

## 常见失败类型（自检时逐项排查）

1. 摘录无法定位（quote 与材料原文不一致）。
2. 来源是二手转引却写成一手的元数据（DOI/页码/卷期未在原文出现）。
3. 撤稿或版本差异未标注（`sources.verification=unverified`）。
4. 把推断写成证实（证据级别与表述不匹配）。
5. 冲突证据只保留单边。
6. 数字/术语/引用有来源但 `source_ref` 悬空（未指向 `sources` 中的条目）。

以上失败均写入 `verification_report.json` 的 `warnings/hard_errors`，修复后再落 `status=complete`。
