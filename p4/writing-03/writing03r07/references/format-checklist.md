# 格式核对口径（题目四问）

本文件是 `result.json` 中四个核对结构的字段规范与判定口径。所有条目**必须有来源定位**：`source_locator`（文件/章节/页码）+ 逐字 `quote`（或 `text`）；无法定位的一律标 `status=unknown` 并记入 `uncertainty.limitations`，不得凭经验猜测。

## 1. template_spec —— 认得准

从投稿模板、学位论文模板或院系补充规定中抽取**可逐项核对的格式规格**，形成检查项清单。常见规格（存在才写，缺失标 `not_reported`）：

| 字段 | 含义 |
|-|-|
| `columns` | 栏数 |
| `font_family` / `font_size` | 字体与字号（revision3 用 Liberation Serif） |
| `line_spacing` | 行距（如 1.5 倍、固定 20pt） |
| `margins` | 页边距（上下左右） |
| `heading_levels` | 标题层级与编号规则 |
| `caption_style` | 图注/表注位置与字号（表注在上、图注在下） |
| `reference_style` | 参考文献样式（字段顺序、作者截断、标点、中英文混排） |
| `export_format` | 导出格式（docx/tex/pdf） |

条目 schema：`{item_id, category, requirement, expected, actual, status, source_locator, quote}`。
`status ∈ {pass, fail, partial, unknown}`。

**版本差异与冲突**：同一规格在多个模板/规定中不一致时，所有来源都记录，`status=conflict`，并把冲突写入 `uncertainty.conflicting_evidence`，不得只保留其一。

## 2. format_checklist —— 排得对

逐项核对**成品是否符合目标模板**，而不是只看外观相似。至少覆盖：

- 标题层级与编号：层级绑定、编号连续性、目录/书签是否随层级生成。
- 图表题注与交叉引用：三线表、题注位置与字号、图/表编号与正文引用号一致。
- 参考文献条目：字段顺序、作者截断（如“et al.”/“等”）、标点、中英文混排。
- 页眉页脚：内容、页码格式、奇偶页差异。

条目 schema 同 `template_spec`；额外允许 `evidence`（哪一处成品内容佐证通过/未通过）。

## 3. content_integrity —— 转得全

登记转换/套用过程中每个**内容单元**的保真状态，逐项比较 `present_before` 与 `present_after`：

- 内容单元类型：正文、图表、公式、脚注、批注、修订、域代码、分节符、嵌入对象、目录。
- 字段：`{unit_id, unit_type, locator, present_before, present_after, changed, note}`。
- `changed ∈ {none, reformatted, modified, deleted, merged, rasterized}`；`modified/deleted/rasterized` 属于**内容被改动**，必须说明原因并进 `pending_human` 或 `uncertainty`。

判定原则：格式变化（换字体、改字号、调整对齐）记 `reformatted`，**不**算内容改动；正文文字、数值、引用被改写或图表被图片化才算 `modified/rasterized`。

## 4. export_verification —— 交得出

导出后逐项实测并回填（做不到就写 `not_verified`，不伪造）：

```json
{
  "file_exists": true,
  "opens_without_error": true,
  "fonts_embedded": true,
  "font_used": "Liberation Serif",
  "page_size": "A4",
  "margins_verified": true,
  "toc_refreshed": true,
  "page_numbers_valid": true,
  "editable_source_kept": true,
  "artifacts": [
    {"path": "/app/output/manuscript.docx", "kind": "editable"},
    {"path": "/app/output/manuscript.pdf", "kind": "preview"}
  ],
  "notes": []
}
```

关键判据：

- 可打开：目标工具/渲染器能打开且无报错。
- 字体嵌入：PDF 元数据或导出日志能佐证字体已嵌入（revision3 用 Liberation Serif）。
- 页码与目录：目录字段刷新后页号与正文一致。
- 可编辑源：md/tex/docx 源文件保留，不把成品仅导出为图片。

## 核对项的通用纪律

1. 每个 `pass/fail/partial` 判定都要能指回一个 `source_locator` + `quote`，否则降为 `unknown`。
2. 检查项覆盖“模板要求 + 原稿内容单元”两个维度；缺材料时 `status=unknown` 并进 `material_gaps`。
3. 冲突不隐藏：同一指标多来源不一致时并列记录并标 `source_conflict`。
4. 禁止为凑满清单而编造条目：没有依据的规格一律 `not_reported`。
