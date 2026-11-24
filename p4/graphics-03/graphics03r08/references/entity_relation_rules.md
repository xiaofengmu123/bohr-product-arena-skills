# 实体、关系、布局、图例与数据编码规则

## 1. 实体命名规范

- 基因：斜体（SVG 中 `font-style:italic`），物种前缀按惯例（人 `h`/`Homo sapiens`，鼠 `m`/`Mus musculus`），符号尽量用官方 HGNC/MGI 命名。
- 蛋白：正体，同义名归并到一个 `id`，别名放入 `aliases`。
- 复合物：`kind=complex`，成员写入 `aliases` 或 `data_mappings`，不与单体蛋白混用。
- 家族：`kind=family`，只代表一组同源成员，不与单个成员画成同一节点。
- 细胞类型 / 亚细胞区室：`kind=cell_type` / `kind=compartment`，不混用。
- 同名基因与蛋白：按上下文（斜体约定、蛋白修饰语境、亚细胞定位）判定；证据不足记 uncertainty。

## 2. 关系语义映射（文字 → 图元）

| 原文提示词 | type | 端点样式 |
|---|---|---|
| 促进、激活、诱导、增强、上调 activate/promote/induce | activation | 实线 + 实心箭头（绿） |
| 抑制、阻断、下调 inhibit/suppress/repress | inhibition | 实线 + T 形端（红） |
| 结合、募集、相互作用 bind/recruit/associate | binding | 虚线 + 无箭头（灰） |
| 磷酸化、泛素化、乙酰化、甲基化 phosphorylate/ubiquitinate | modification | 实线 + 箭头（紫），modifier 记录修饰类型 |
| 转运、转位、分泌、入核 transport/translocate | transport | 实线 + 箭头（蓝） |
| 转化、生成、变成 convert/generate | conversion | 双向箭头（橙） |

`direction` 默认为上游→下游；材料明确写反向（如“被 X 抑制”指向主语）时按语义定方向并记 evidence。修饰的 `modifier` 写具体修饰（phosphorylation 等）。

## 3. 通路方向与区室布局

- 默认分层顺序：extracellular → membrane → cytosol → organelle → nucleus（自上而下）。
- 跨膜受体：`transmembrane:true`，画在 membrane 带，跨膜两侧分别连接胞外配体与胞内效应器。
- 上游在前（左），下游在后；分支并列；反馈环画成回折边并单独标注。
- 无方向信息时关系 `direction=unknown`，写入 uncertainty，不臆断流向。

## 4. 图例与数据编码

- 图例由 `build_svg`/`main.py build` 从实际使用的 relation type 与 node kind 自动生成，杜绝图例与图面不一致。
- 颜色、线宽、节点大小、形状到数据（表达量、显著性、置信度、时间点）的映射必须写进 `data_mappings`，每项带 `sources[{locator, quote}]`。
- 无来源的数值不标注；若材料数值与图示矛盾，记入 uncertainty 并暂停该项。

## 5. 导出与字体

- SVG 用可替换字体栈：`Arial, Helvetica, 'Noto Sans', 'Noto Sans CJK SC', sans-serif`。
- PDF 优先平台 office-renderer/browser-automation；无工具时用 `main.py build` 的 stdlib 矢量 PDF（base-14 Helvetica，非 Latin-1 标签以 ASCII id 回退并记录 font_check）。
- PNG 只记录真实生成的文件；不可渲染时如实降级。
