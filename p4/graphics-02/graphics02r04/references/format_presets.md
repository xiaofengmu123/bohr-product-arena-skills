# 导出预设与源数据映射约定

本文件给出可复用的样式规则与导出参数，供 Agent 与 `scripts/render_svg.py` 共同遵循。所有数字为默认值，可按目标期刊/模板覆盖；覆盖时把差异记入 `result.json` 的 `sources`（导出参数）与 `change_log`。

## 1. 导出预设

| 场景 | 尺寸 | 分辨率/格式 | 颜色空间 | 字体 | 背景 | 矢量文字 |
|---|---|---|---|---|---|---|
| 期刊单栏 | 89 mm 宽 | PDF 矢量 + PNG 600 dpi | RGB | 嵌入 | 白 | 是（不转曲） |
| 期刊双栏 | 183 mm 宽 | PDF 矢量 + PNG 600 dpi | RGB | 嵌入 | 白 | 是 |
| 汇报/海报 | 16:9 或 4:3 | PDF + PNG 300 dpi | RGB | 嵌入 | 白/透明按需 | 是 |
| 演示文稿 | 幻灯片尺寸 | PPTX 可编辑 | — | 嵌入 | 白 | 是 |

- PNG：真实渲染预览，非占位符；≥300 dpi（期刊 600 dpi）。
- PDF：矢量文字可选中；字体嵌入；尺寸与留白符合目标模板。
- SVG：`<text>` 节点保文字，`<g id>` 分层，`data-*` 属性溯源。
- 中文字体回退顺序：Noto Sans CJK SC → Source Han Sans SC → Microsoft YaHei → PingFang SC → SimSun → sans-serif（由 `font-discovery` 确认实际可用项）。

## 2. 源数据映射约定

- 图元属性：`data-source`（源文件）、`data-row`（行号，1-based）、`data-col`（列号，0-based）、`data-value`（编码值）。
- `evidence_map` 条目：`claim`（结论）、`locator`（`文件名#col列号` 或 `文件名#p段号`）、`quote`（逐字片段）、`kind`（`data_encoding|entity|relation|style|annotation`）、`status`（`traced|insufficient|source_conflict`）。
- 冲突处理：同一指标多源不一致→两条都记，`status=source_conflict`，不擅自取一舍一。
- 误差线：必须写明 `error_type ∈ {SD,SEM,CI}` 与样本量 `n`；缺失则图注写 `not_reported`。

## 3. 图型最小字段（供 figure_spec.json 参考）

- bar/line/scatter：`categories`、`series[{name,values,errors,error_type,n}]`、`significance[{i,j,mark,p,y}]`。
- heatmap：`categories`（行标签）、`series[{name,values}]`（列为系列），色阶由脚本归一化。
- 机制图/流程图：实体（id/name/type/locator/quote）、边（source/target/type，type ∈ 激活/抑制/转化/结合）、子图布局；用 SVG `<g>` 分层，文字保持 `<text>`。

## 4. 复用与版本

- 预设与清单是“可复用配置”而非一次性手工调整：改图时应改 spec 或样式参数，重跑脚本得到一致结果。
- 脚本版本在 `scripts/` 内；每次导出把工具与版本写入 `result.json` 的 `sources`。
