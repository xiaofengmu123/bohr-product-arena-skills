# 规格项检查清单与符合性口径

本清单是 `template profile`（模板规格表）的填写口径。每项规格只写模板明文写出的值；模板未写明的标 `not_specified`，无法实测的标 `not_checkable`，不得假设默认值。

## 1. 规格域与检查项

### page（页面）
| 检查项 | 说明 | 常见来源 |
|-|-|-|
| page_size | 纸张尺寸（A4/Letter/自定义） | 模板文档页面设置 |
| margins | 页边距（上下/左右，cm 或 in） | 页面设置或院系规定 |
| columns | 栏数（单栏/双栏） | 模板排版说明 |
| header_footer | 页眉页脚（奇偶页、页码位置） | 模板页面设置 |

### typography（字体与排版）
| 检查项 | 说明 |
|-|-|
| font_family | 正文/标题字体族（中英文分别记录） |
| font_size | 字号（pt/磅/号） |
| line_spacing | 行距（倍行距/固定值） |
| paragraph_spacing | 段前段后距 |

### heading（标题层级）
| 检查项 | 说明 |
|-|-|
| heading_levels | 层级数量与每级样式 |
| numbering_rule | 编号规则（1 / 1.1 / 一、） |
| style_binding | 标题与 Word 样式/LaTeX 命令绑定 |
| toc_bookmark | 目录与书签生成方式 |

### figure_table（图表与参考文献）
| 检查项 | 说明 |
|-|-|
| caption_position | 图题在下、表题在上 |
| caption_font_size | 题注字号 |
| numbering_rule | 图表编号与正文交叉引用一致性 |
| table_style | 三线表/网格表 |
| reference_style | 参考文献样式（GB/T 7714、APA、MLA 等）与字段顺序、作者截断、中英文混排 |

### export（导出结果）
| 检查项 | 说明 |
|-|-|
| export_format | 目标格式（pdf/docx/tex） |
| font_embedding | 字体是否嵌入 |
| page_numbers | 页码是否可用 |
| toc_refresh | 目录是否刷新 |
| editability | 是否保留可编辑源文件 |

## 2. 符合性分级（compliance）

| 取值 | 含义 | 何时使用 |
|-|-|-|
| pass | 实测值等于模板要求值 | 有实测且一致 |
| fail | 实测值不等于模板要求值 | 有实测且不一致 |
| pending | 已记录要求但尚未实测 | 骨架/进行中 |
| not_specified | 模板未明文写出该值 | 模板未规定 |
| not_checkable | 有要求但当前工具无法实测 | 缺字体发现/渲染工具 |

## 3. 证据强度口径（evidence_level）

| 级别 | 含义 | 示例 |
|-|-|-|
| template_rule | 模板明文规定 | 模板写明“小四、1.5 倍行距” |
| draft_evidence | 稿件内容自身证据 | 正文数字、引用原文 |
| auto_hint | 脚本正则自动抽取，需人工复核 | `confidence=low` |
| human_inference | 由人工从材料推断 | 多处规定综合判断 |
| unverified | 来源无法核验 | 仅二手转引 |

## 4. 内容单元保真（content-unit anchor）

- 覆盖类型：`paragraph / heading / table / figure / formula / footnote / comment / revision / field / toc / reference_entry`。
- `preserved` 三态：`true`（逐字保留）、`false`（缺失或改写，必须写明差异）、`reformatted`（仅格式变化，内容不变）。
- `transform` 枚举：`none / renumbered / renamed / reformatted / split / merged / dropped`；`dropped` 必须写进 `version_boundary.material_gaps`。
- 每单元必须能由 `quote` 在源材料中定位，无法定位的标 `limitations`。

## 5. 填写顺序建议

1. 先填 `page → typography → heading`（决定全局版式）。
2. 再填 `figure_table → export`（决定图表、引用与交付）。
3. 每项同时写 `source_locator + quote`，保证“要求从哪来”可回查。
4. `auto_hint` 项必须人工确认后才能标 `compliance`，否则保持 `pending`。
