# 图面语法与导出预设（figure-grammar.md）

本文件规定实体、关系、布局、图例映射与导出参数的图形语法。生成 SVG 时逐条遵循；不确定处标注「推测」并写入 uncertainty。

## 1. 实体类型

- `node`：单个科学实体（基因、蛋白、细胞、化合物），用圆角矩形或胶囊，`id="node:<name>"`。
- `group`：包含/分组边界（细胞区室、通路模块、样本组），用虚线或浅色底框包裹成员，`id="group:<name>"`。
- `process`：过程/步骤（激活、磷酸化、步骤序号），用圆角矩形+箭头入出。
- `annotation`：标注（浓度、时间、单位、子图编号），用细线指引，`id="note:<id>"`。

## 2. 关系类型与视觉语法（方向不可反）

| 关系 | 视觉语法 | 错误形态 |
|-|-|-|
| 激活/促进 | 实线，尖箭头 → | 画成 ⊣ 或裸线 |
| 抑制/阻断 | 平头短线 ⊣ | 画成 → |
| 包含 | 分组框包围成员，成员在框内 | 画成并列排布 |
| 并列 | 同层水平对齐，等间距 | 画成嵌套 |
| 上下游 | 自上而下或自左而右，箭头顺流 | 倒置 |
| 因果 | 单箭头，原因→结果 | 双向 |
| 时序 | 横向时间轴，步骤从左到右编号 | 乱序 |

每类关系在 provenance 里写清楚「材料原文的哪个动词/结构」判定为该类型（如 `inhibits → ⊣`）。

## 3. 布局规则

- 分区：按功能分 `group`，组内成员在组框内；跨组箭头不穿越文字。
- 对齐：同层元素水平或垂直对齐，间距一致。
- 字号层级：标题 > 分组标签 > 实体标签 > 标注；小尺寸印刷下最小字号 ≥6pt（导出 300dpi 时折算）。
- 避让：文字不重叠、引导线不交叉；拥挤处改用编号+图例。

## 4. 图例与数据映射（同源）

- 图例项必须来自 entities 清单的分类字段，**legend 与 entities 同源**，不在图例里手写新类别。
- 颜色映射：分类数目 == 颜色数目；每条映射写入 provenance.mapping（如 `#d62728 ↔ 处理组`）。
- 标记形状：分类用不同形状或线型，与数据表分类严格对应。
- 数值与单位：图中出现的每个数值/单位必须能在材料中找到逐字出处，缺失则 `not_reported` 并标注。

## 5. 导出预设（期刊参数检查）

| 参数 | 目标值 | 检查方式 |
|-|-|-|
| 尺寸 | 按目标期刊（无指定时单栏 90mm 或 180mm 双栏） | export_files.width_px/height_px |
| 分辨率 | 位图 ≥300dpi；矢量无损 | export_files.dpi |
| 字体嵌入 | PDF 嵌入 / SVG 声明 font-family 可替换 | export_files.font_check=embedded/replaced/none |
| 线宽 | 印刷 ≥0.5pt，箭头清晰 | 图面检查 |
| 色彩空间 | RGB（在线）/ CMYK（部分印刷），标注一致 | export_files.color_space |

导出后运行 `python scripts/main.py validate --output /app/output`；`font_check=none` 时必须写 uncertainty 说明替代方案。

## 6. 分层骨架模板

`references/svg-layers-template.svg` 提供空内容的分层骨架（layer-groups / layer-nodes / layer-edges / layer-labels / layer-legend），并预置激活箭头 `arrow-positive`、抑制平头 `arrow-negative`、字体回退与图层样式。仅作结构起点：实体、关系、图例内容必须全部来自来源材料并逐条写 provenance，禁止把模板当插画直接提交。

