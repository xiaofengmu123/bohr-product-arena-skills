# 模板规格与四问检查框架

本文件是"学术格式与模板适配"的四问方法论：**认得准 → 排得对 → 转得全 → 交得出**。每一问产出一类可逐项核对的检查项；检查项只写"能从材料或实测中证实的值"，不得凭经验猜测。猜测值必须写进 `uncertainty.claims_requiring_confirmation`。

## 1. 认得准：规格抽取（spec extraction）

从目标模板（`.docx`/`.tex`/`.pdf`/学校补充规定）中逐项抽取结构化规格，形成 `style_spec` 检查项清单。每项含：`field`（字段）、`required`（要求值）、`observed`（材料中实测值）、`locator`（模板/规定中的定位）、`quote`（逐字摘录）、`status`（`met | unmet | conflict | unverified`）。

| 类别 | 可核对字段 |
|---|---|
| 页面 | page_size、margins（上下左右）、columns（栏数）、gutter、line_spacing、first_line_indent |
| 字体 | body_font、heading_font、font_size（正文/各级标题）、cjk_font、latin_font、font_embedding |
| 标题层级 | heading_levels（1–n）、numbering_scheme、style_binding（Heading 1…n）、toc_generation、bookmark |
| 图表 | figure_caption_position、table_caption_position、caption_font_size、three_line_table、numbering_style、cross_reference |
| 参考文献 | citation_style（GB/T 7714 / APA / 期刊样式）、field_order、author_truncation（etal 规则）、punctuation、中英文混排 |
| 页眉页脚 | header_content、footer_content、page_number_position、odd_even、section_breaks |
| 导出 | target_format、font_embedding、toc_refresh、page_number_refresh、editable_source_kept |

抽取来源只认实际文件内容，不按文件名或领域标签脑补；模板版本差异、补充规定与模板冲突的项并列记入 `uncertainty.conflicting_evidence`，正文按"存在争议"处理，不偏向单边。

## 2. 排得对：样式映射与内容锚定（style mapping）

把原稿的内容单元映射到目标模板，逐项对照 `style_spec`，只改样式绑定而不改内容文本：

- 标题层级：按"语义层级→样式名"映射，禁止只改字号/加粗而不绑定 `Heading N` 样式；目录与书签必须由样式派生，避免层级错位。
- 编号：章节、图表、公式编号采用目标模板的编号规则，正文交叉引用与编号联动更新，不一致处标 `unmet`。
- 参考文献：条目字段顺序、作者截断、标点、中英文混排逐条按样式规则重排；每条保留原条目原文，改动写入 `version_boundary.diff`。
- 图表题注：图注位置（图下/表上）、字号、三线表规范、编号与正文引用一致。

## 3. 转得全：内容单元保真（content preservation）

转换或套用前后，逐类内容单元都要"未删除、未合并、未改写、未图片化"。每个内容单元用 `unit_id + source_locator + quote` 锚定，产出 `fact_source_map`。必须覆盖的内容单元类型：

正文段落、标题、图表、公式、脚注、尾注、批注、修订、域代码、分节符、目录、页眉页脚、交叉引用、嵌入对象、题注。

每类单元的锚定记录含 `unit_id / unit_type / source_locator / quote / transform（keep|mapped|renumbered|reflowed）/ evidence_level / verified`。`transform != keep` 的条目必须写清改动理由并计入 `version_boundary.diff`。

## 4. 交得出：导出校验（export validation）

导出后实测以下项，逐条回填 `deliverable.export_checks`：

- 文件可打开：PDF 以 `%PDF-` 开头、Office 文件可解析、无损坏。
- 字体嵌入：`pdffonts`/`font-discovery` 检查嵌入状态；缺失字体列出替代（如题面指定的 Liberation Serif）。
- 页码与目录：目录页码与正文实际页码一致；域代码已刷新。
- 可编辑性：保留 `.docx`/`.tex` 可编辑源文件，导出件不因图片化而失去文本。
- 可复核性：每条 `fact_source_map` 的 `quote` 能经 `scripts/main.py verify` 在材料文本中定位。

## 检查项 JSON 结构（写入 result.json）

```json
{
  "style_spec": [
    {"field": "body_font", "required": "Liberation Serif", "observed": "SimSun",
     "locator": "template.docx#styles.xml", "quote": "正文使用 Liberation Serif",
     "status": "unmet"}
  ],
  "content_units": [
    {"unit_id": "cu-001", "unit_type": "figure_caption",
     "source_locator": "manuscript.docx#fig1", "quote": "图 1 实验装置",
     "transform": "renumbered", "evidence_level": "direct_observation", "verified": true}
  ]
}
```

`content_units` 可并入 `fact_source_map`；二者语义一致，都是"可逐条回溯的锚定记录"。
