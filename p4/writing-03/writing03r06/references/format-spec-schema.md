# 模板规格字段口径（format_spec.json）

本文件定义 `intermediates/format_spec.json` 的字段含义与取值口径。目标是“认得准”：每一项规格都能指回模板文件或补充规定中的位置（`locator`），而不是凭经验猜测。

## 1. 字段口径

| 字段 | 含义 | 允许取值/示例 |
|---|---|---|
| `page.page_size` | 页面尺寸 | A4 / Letter / 16开 等 |
| `page.orientation` | 方向 | portrait / landscape |
| `page.columns` | 栏数 | 1 / 2 |
| `page.margins` | 页边距（top/bottom/left/right） | 单位 mm 或 in，注明单位 |
| `page.line_spacing` | 行距 | 1.5倍 / 固定值 22磅 等 |
| `page.body_font_size_pt` | 正文字号（磅） | 10.5 / 12 等 |
| `page.body_font` | 正文字体 | 中文字体 + 西文字体（如 宋体/Times New Roman） |
| `headings[]` | 标题层级规格 | level/style/numbering/font_size_pt/bold/alignment |
| `figures.caption_position` | 图注位置 | 图下方 / 图上方 |
| `tables.caption_position` | 表注位置 | 表上方 / 表下方 |
| `tables.style` | 表格样式 | 三线表 / 全线表 |
| `references.style` | 参考文献样式 | GB/T 7714 / APA / IEEE / 期刊指定格式 |
| `headers_footers` | 页眉页脚 | 页眉内容 / 页码位置 |
| `export.format` | 导出格式 | docx / pdf / tex |
| `export.font_embedding` | 字体是否嵌入 | true / false / unknown |

## 2. 填写规则

1. 每条规格只填“能从材料中逐字指认”的值；不能指认的填 `null`，并把该条目写入 `uncertain`。
2. 规格的定位写入该条目的 `locator`（如 `template.docx#样式-正文`、`规定.pdf#p3`）。
3. `check_items[]` 是最终逐项核对清单：`{id, item, value, locator, verified}`，核对后把 `verified` 置 true。
4. 同一项出现相互冲突的规定时，不选边，全部写入 `conflicts[]`，并注明各来源定位；正文按最严格或题面明确要求执行并在 `resolution` 记录。

## 3. 常见失败类型

| 失败 | 表现 | 后果 |
|---|---|---|
| 凭文件名猜模板 | 拿“期刊名.docx”猜样式 | 字号/行距/题注位置错 |
| 只改字号与加粗 | 标题用空格顶编号、不改样式绑定 | 目录/书签层级错位 |
| 忽略中英文混排 | 中文标点混用西文样式 | 参考文献标点/作者截断错 |
| 图注表注位置颠倒 | 图注写表上方 | 图表编号与题注错位 |
| 忽略分节符/页眉页脚 | 只改正文 | 页码/页眉不符合目标模板 |
| 版本差异未处理 | 新旧规定混用 | 部分检查项按旧版执行 |

## 4. 最小可用判断

- 有 `check_items` 且每项带 `locator` → 可复核。
- `uncertain` 与 `conflicts` 非空但未被正文引用 → 隐藏冲突，属质量缺陷。
- 全部规格为 `null` 且 `status=unknown` → 无模板可依，按题面明确约束降级执行并标注。
