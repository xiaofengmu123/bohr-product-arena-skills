# 转换完整性与导出可用性检查清单（integrity-checklist.md）

本文件用于 `fidelity_report.json` 的逐项填写与 `content_units.json` 的 `status` 判定。目标是“转得全、交得出”：内容单元不丢失、不改写、不图片化，导出文件可用且可复核。

## 1. 内容单元类型与保留要求

| 单元类型 | 必须保留的单元 | 允许的合理处理 |
|---|---|---|
| heading | 标题文本与层级 | 按目标模板重排编号，但不改语义 |
| paragraph | 正文完整文字 | 只改样式，不改字词（除题面明确要求改写） |
| figure | 图片与图注 | 允许重排位置/字号，不允许删除或压缩为占位 |
| table | 表格内容与表注 | 允许改三线表样式，不允许合并/删行 |
| formula | 公式本体 | 允许换排版方式，不允许符号被改写 |
| footnote | 脚注内容与锚点 | 允许移位，不允许丢失 |
| comment / revision | 批注与修订 | 若题面要求“接受修订”，记录每条被接受/拒绝 |
| field_code | 域代码（页码/交叉引用/目录） | 转换为目标模板对应域，不可静态化为死文本 |
| toc | 目录与书签 | 允许刷新，不允许删除书签 |
| reference | 参考文献条目 | 允许重排字段顺序，不允许丢字段或作者 |

## 2. 状态判定口径

- `kept`：内容与锚点均保留，仅样式变化。
- `mapped`：内容保留，编号/样式/位置按目标模板重映射。
- `missing`：内容在导出物中丢失（必须列入 `lost_units`，结论至少为 `partial`）。
- `dropped`：有意删除（必须写 `reason` 与依据，否则视为 `missing`）。
- `pending`：尚未处理（交付前必须清零或列入 `pending_human`）。

## 3. 转换前后比对（fidelity_report）

- `before_counts` 与 `after_counts` 逐类计数；任何类别计数下降都必须有 `lost_units` 对应条目。
- 被改写的正文单元列入 `modified_units`，并标注 `intentional`（是/否）与依据；非有意的改写是内容正确性缺陷。
- `artifact_checks` 对每个导出文件记录：能打开、字体嵌入、页码/目录可用、可编辑源文件保留。

## 4. 导出可用性逐项

- [ ] 导出文件能正常打开（docx/tex/pdf 分别验证）
- [ ] PDF 以 `%PDF-` 开头、页数与页码一致
- [ ] 字体已嵌入或已在 `uncertainty.limitations` 说明替代字体
- [ ] 目录与页码已刷新，书签未丢失
- [ ] 交叉引用与图表编号一致（无“图 ?？”或断链）
- [ ] 可编辑源文件（md/tex/docx）与渲染预览（pdf/png）路径已回填 `result.json`

## 5. 最小可用判断

- `fidelity_report.conclusion=pass` 且 `before_counts == after_counts`（在无有意删除前提下）→ 转得全。
- 存在 `missing` 单元但未列入 `lost_units` → 隐藏丢失，属正确性缺陷。
- 导出文件无法打开 → `conclusion=fail`，`status` 至少 `partial`，写 `pending_human`。
