# 证据强度分级与摘录口径

本文件规定 `fact_source_map` / `template_spec` / `format_checklist` / `content_integrity` 中“依据在哪、依据有多强”的书写口径。

## 1. 逐字摘录与定位（硬要求）

- 每个关键结论、数值、术语、图表、引用必须有 `locator` + 逐字 `quote`。
- `locator` 形式：`文件名#章节/页码` 或 `文件名#段落号`，例如 `paper.md#3.2 结果`、`template.docx#p2`；PDF 抽取后可用 `文件名.pdf#p3`。
- `quote` 必须是从材料中**原样复制**的片段，不得改写、不得用近义转述冒充摘录；摘录过短无法定位时扩到足够长度。
- 找不到原文时，该论断降级为“未证实”，写 `evidence_level="unverified"` 并进 `uncertainty.claims_requiring_confirmation`。

## 2. 证据强度分级（evidence_level）

| 级别 | 含义 | 适用 |
|-|-|-|
| `primary_original` | 原始研究/原始数据直接支持 | 数字、图表、方法出自原始论文正文 |
| `primary_quote` | 原文逐字引用支持 | 定义、结论句直接引文 |
| `secondary_summary` | 综述/指南的归纳 | 领域共识、综述转述 |
| `guideline` | 模板/规定/官方规范 | 格式要求、字体、页边距、参考文献样式 |
| `inferred` | 由材料合理推断 | 必须写明推断前提 |
| `unverified` | 无法从给定材料证实 | 需人工确认或补充材料 |

## 3. 表述强度匹配（说得准）

- 相关 ≠ 因果：观察性关联不得写成“导致/使”。
- 单篇小样本 ≠ 领域共识：单中心小样本写成“该研究初步表明”，不写成“领域公认”。
- 冲突证据不隐藏：同一指标多来源不一致，全部并列并标 `source_conflict`，正文用“存在争议/尚不明确”。
- 已确立结论用 `表明/证实`；初步证据用 `初步表明`；展望用 `有望`，不得提前拔高为已实现。

## 4. 不确定性与降级口径

- `claims_requiring_confirmation`：只有摘要、无正文、无统计量、版本不明的论断。
- `conflicting_evidence`：来源间矛盾、模板版本冲突、原文与摘要矛盾。
- `material_gaps`：缺模板、缺某章节、缺图表源、缺字体、缺全文。
- `limitations`：软件不可用、预览未生成、引用无法核验、超时未完成的部分。

## 5. 常见失败类型（自查重点）

1. 摘录不可定位：`quote` 与材料原文对不上 → `main.py --verify` 会报 `quote_not_located_in_text_materials`，需回写 `limitations` 或修正摘录。
2. 二手转引：把综述里的引用当作原始出处 → 标 `secondary_summary` 并注明“据 X 综述转引，未核对原文”。
3. 版本差异：模板 revision 与历史版本并存 → 全部记录，标明当前适用版本（revision3）。
4. 编造来源：写不存在的 DOI/页码/作者 → 一律禁止；无元数据写 `not_reported`。
5. 缺证据链：只有结论没有 `quote/locator/evidence_level` → 每个实质论断三要素缺一不可。
