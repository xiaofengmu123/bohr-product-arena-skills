# 机制图规则与对照清单

本文是实体、关系、布局、图例、命名的可执行规则，供抽取与建模阶段逐条核对。渲染器的常量表（`scripts/core_impl.py`）与此处一致，因此图例、颜色、形状与图面由同一事实源生成，不会漂移。

## 1. 实体命名与归类

| 概念 | `kind` | 形状 | 文字样式 | 判定要点 |
|---|---|---|---|---|
| 基因 | `gene` | 矩形 | 斜体 | 指基因座位/转录单位时用斜体（如 *TNF*） |
| 蛋白 | `protein` | 圆角矩形 | 正体 | 指蛋白产物时正体（如 TNF） |
| 受体 | `receptor` | 圆角矩形 + 跨膜线 | 正体 | 跨膜受体 `transmembrane:true`，置于膜带 |
| 复合物 | `complex` | 圆角矩形（双边框） | 正体 | 多亚基装配体，勿与单个蛋白混用 |
| 蛋白家族/同源组 | `family` | 虚线圆角矩形 | 正体 | 表一类蛋白，勿与单个成员混用 |
| 细胞类型 | `cell_type` | 椭圆 | 正体 | 如 T 细胞、巨噬细胞 |
| 亚细胞区室/细胞器 | `compartment` | 虚线大矩形 | 正体 | 如溶酶体、线粒体（位于父区室内） |
| 小分子/配体 | `small_molecule` | 圆形 | 正体 | 如 ATP、Ca²⁺、配体 |

规则：

- 基因/蛋白同名：按上下文证据判定，证据不足列为警告并写入 `uncertainty`。
- 物种前缀：跨物种讨论时标注（如 `Hs-`, `Mm-`），单一物种在 `species` 字段写明。
- 同义名/旧名：合并到 `synonyms`，图面只画规范名，不重复画同义节点。
- 每个实体必须有 `id`、`label`、`kind`、`compartment`；除明显公理外都要有 `sources`。

## 2. 关系语义与箭头

| 中文表述 | `type` | 箭头/端点 | 颜色 |
|---|---|---|---|
| 促进、激活、上调、诱导 | `activation` | 实线三角箭头 → | 绿 #2e7d32 |
| 抑制、阻断、下调、拮抗 | `inhibition` | 实线 T 形条 ⊣ | 红 #c62828 |
| 结合、相互作用、招募 | `binding` | 无箭头或双圆点 | 灰 #546e7a |
| 磷酸化、泛素化、乙酰化等 | `modification` | 圆点端点 + 修饰词标签 | 紫 #6a1b9a |
| 转运、易位、入核、分泌 | `transport` | 虚线箭头 | 蓝 #1565c0 |
| 转化、生成、裂解为 | `conversion` | 双向箭头 | 橙 #ef6c00 |

规则：

- 每个关系必须显式 `type`；`source`→`target` 与箭头方向一致（`direction=reverse` 仅当原文明确反向）。
- `modification` 必须填 `modifier`（phosphorylation / ubiquitination / …）。
- 反馈环用 `direction=feedback`；自环（自磷酸化）在 `uncertainty` 说明。
- 未证实关系不画；`evidence=uncertain` 或 `inferred` 在 `uncertainty` 记录证据强度。
- 每个关系都要有 `sources`（locator + quote），缺失在 warnings 与 `uncertainty` 记录。

## 3. 通路方向与区室布局

- 默认自上而下层级：`extracellular` → `membrane` → `cytosol` → `nucleus`。
- 跨膜受体：`compartment=membrane`、`transmembrane:true`，图元跨越膜带双线。
- 通路方向必须符合生物学流向（受体→胞内→核内）；分支、并行支路、反馈环走向明确。
- 区室错位是硬错误：受体画在膜内、转录因子画在胞质等都算画错。
- 关系上下游顺序由 `source`→`target` 表达；布局确定性来自 `compartment` 排序 + 实体稳定排序。

## 4. 图例与数据编码

- 图例由实际使用的 `relation.type` 与 `entity.kind` 自动补全，禁止手绘图例导致与图面漂移。
- 数据编码通道：颜色 `fill_color`、线宽 `line_width`、节点大小 `node_size`、形状 `shape`。
- 每个 `data_mappings` 必须带 `source`；表达量/显著性/置信度/时间点无来源不得标注数值。
- 图例、图面、数据三者必须一致；冲突信息两条都记录并标 `source_conflict`。

## 5. 证据溯源

- 关键结论/数值/图元 → `sources[]`，每项含 `locator`（如 `materials/paper.md#L12`）+ 逐字 `quote` + `material`。
- 冲突不隐藏；不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 6. 导出与文件规范

- SVG：UTF-8、可被 XML 解析、含 `layer-background/compartments/relations/entities/labels/legend` 命名图层。
- 字体：SVG 用可替换 `font-family="sans-serif"`；导出时字体不可用如实记录 `font_check`。
- PNG/PDF：只在真实生成后写入 `export_artifacts`；PDF 魔数 `%PDF-`；PNG 记分辨率/尺寸。
- 文件路径互相对应：源文件、导出件、result.json 均写入 `/app/output/`。

## 7. 快速核对表（建模时逐条过）

1. 每个实体都归类到正确的 `kind` 与 `compartment`。
2. 每个关系 `type` 与原文动词对应，箭头方向正确。
3. 跨膜受体 `transmembrane:true` 且在膜带。
4. 图例项覆盖所有用到的关系类型与实体种类。
5. 每条实体/边有 `sources[].locator + quote`。
6. 无来源数值不标注；歧义写入 `uncertainty`。
7. SVG 有命名图层；PNG/PDF 真实生成。
8. `change_log` 首条为骨架落盘。
