# 实体/关系/布局/图例规则（figure_rules）

本文件是 `scripts/figure_model.py` 中硬编码规则的说明文档，供 Agent 在撰写
`plan.json` 时对照，也供复核者核对图面语义。脚本只做确定性归一化与样式，
不替 Agent 做生物判断；Agent 的 `semantic_hint` 优先于脚本词表。

## 1. 实体命名与类型

| type | 形状 | 标签样式 | 说明 |
|---|---|---|---|
| `gene` | 圆角矩形 | 斜体 | 基因/位点，如 *AKT1* |
| `rna` | 圆角矩形 | 斜体 | RNA/mRNA/miRNA |
| `protein` | 圆角矩形 | 正体 | 蛋白/酶/受体/转录因子/激酶 |
| `complex` | 椭圆 | 正体 | 多亚基复合物 |
| `family` | 椭圆 | 正体 | 家族/家族成员组 |
| `cell_type` | 椭圆 | 正体 | 细胞类型 |
| `small_molecule` | 圆形 | 正体 | 配体/代谢物/小分子 |
| `process` | 菱形 | 正体 | 过程/表型 |
| `unknown` | 圆角矩形 | 正体 | 未指明类型（需 uncertainty 说明） |

同名基因与蛋白（如 *AKT1* 与 AKT）必须拆成两个实体：基因用斜体、蛋白用正体，
`aliases` 与 `sources` 分别标注；上下文证据不足时写 `uncertainty`。物种前缀、
旧名/同义名归并进 `aliases`，不混用复合物与家族成员。

## 2. 关系语义与图形样式

| type | 线型 | 箭头 | 语义 |
|---|---|---|---|
| `activation` | 实线 | 实心三角箭头 | 激活/促进/上调 |
| `inhibition` | 实线 | T 形端 | 抑制/阻断/下调 |
| `binding` | 实线 | 无箭头 | 结合/相互作用/募集 |
| `modification` | 实线 | 实心箭头 + 修饰符标注(P/Ub/Me/Ac) | 磷酸化/泛素化/甲基化/乙酰化 |
| `transport` | 虚线 | 空心箭头 | 转运/转位/入核/出核 |
| `conversion` | 实线 | 双箭头 | 转换/催化生成产物 |
| `expression` | 虚线 | 实心箭头 | 基因→RNA/蛋白表达 |
| `unknown` | 点线 | 空心箭头 | 未指明（需 uncertainty 说明） |

Agent 未给 `semantic_hint` 时，脚本按 `predicate_text` 词表推断并把来源记为
`semantics_source="lexicon"`；给了显式判断则记为 `"agent"`。方向由
`direction_hint` 决定，缺省 `subject->object`。证据强度写入 `evidence`
（high/medium/low），低置信关系画虚线边框并进 warning。

## 3. 通路方向与区室布局

区室按固定顺序自上而下排列：
`extracellular → membrane → cytosol → endosome → golgi → er → mitochondria → nucleus`。

- 受体类实体 `layout_hints` 标 `transmembrane: true`，节点纵跨膜区带。
- 未给区室提示的实体默认 `cytosol` 并记录 warning（不臆测核内/膜上定位）。
- 反馈环（target 在 source 上游）自动以右侧弧线绘制，写入
  `layout.feedback_loops`。
- 布局由 `_compute_layout` 确定性计算：每个区室一个横带，实体按提及顺序
  从左到右、按行换列摆放，无随机。

## 4. 图例与数据编码

图例由实际用到的实体类型、关系类型与数据编码自动生成，保证与图面自洽。
数据映射（表达量/显著性/置信度/时间点）每条必须有 `sources`：
- 节点填充色/边框 → 实体类型
- 线型与箭头 → 关系类型
- 节点旁数值徽标 → 数据值（`data_items` 的 `value`+`unit`）
- 低置信 → 虚线边框

无来源的数值/颜色/线宽不标注、不画，写入 `uncertainty`。

## 5. 可编辑源文件与导出

- SVG：命名图层 `layer-compartments/layer-relations/layer-entities/layer-data/
  layer-legend`，每个图元带 `data-entity-id`、`data-relation-id`、
  `data-source-locator` 溯源属性；字体用可替换 `font-family` 栈。
- PDF：`scripts/figure_model.py` 直接生成最小合法矢量 PDF（内置 Helvetica；
  CJK 标签在 PDF 中退化为 `?`，SVG 为准，font_check 如实记录）。
- PNG：由平台 `browser-automation` 光栅化 SVG 后经 `--register-export` 注册；
  不可用时 PNG 项不写，`limitations` 说明。
