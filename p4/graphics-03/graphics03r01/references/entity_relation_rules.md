# 实体 / 关系 / 布局 / 图例规则清单

供抽取阶段（写 spec.json）逐条对照；`main.py validate-spec` 只做结构校验，科学判定由 Agent 依据本清单做出。

## 1. 实体命名与消歧

- **基因 vs 蛋白**：基因斜体（SVG `font-style="italic"`），蛋白正体。同一符号既指基因又指蛋白时按上下文判定；证据不足 → `uncertainty` 记 `gene_protein_ambiguous`，不混用。
- **物种前缀**：人类基因 `EGFR`，小鼠 `Egfr`，蛋白 `EGFR`/`Egfr`（随规范）；材料明确给出物种时写入 `species`，否则 `species: null` 并记 uncertainty。
- **同义名归并**：同一实体的旧名/别名写入 `synonyms`，图面只画一个节点，`canonical_name` 给出首选名，避免异名同义重复画。
- **复合物 vs 家族**：复合物用 `complex`（六边形，成员固定）；家族用 `family`（虚线矩形，成员不逐一展开）。不把家族成员误当成同一蛋白。
- **细胞类型 vs 区室**：`cell_type` 是细胞类型节点；`compartment` 是实体所在区室，两者不混用。跨膜受体 `transmembrane: true` 且放在 membrane 列。
- **实体 ID**：`id` 唯一、稳定；图面与 result.json、evidence_map 的 `element_ref` 用同一 id。

## 2. 关系语义映射（文字 → 类型）

| 材料措辞示例 | 类型 | 端点/线型 |
|---|---|---|
| 促进/激活/诱导/上调/增强 | `activation` | 实线 + 实心箭头 |
| 抑制/阻断/下调/拮抗/减弱 | `inhibition` | 实线 + T 型短线（bar） |
| 结合/相互作用/复合/对接 | `binding` | 无箭头实线 |
| 磷酸化/泛素化/甲基化/乙酰化等 | `modification` | 点状箭头 + `modifier` 标注（如 `P`/`Ub`） |
| 转运/入核/分泌/出膜/移位 | `transport` | 虚线 + 箭头 |
| 转化/互变/A→B | `conversion` | 双箭头 |
| 募集/招募/定位到 | `recruitment` | 点线 + 箭头 |

- **方向**：`direction` 写 `source -> target`，与箭头一致；材料只写"相互作用"无方向 → `binding` 且 `direction: null`，或 `uncertainty` 记 `direction_unknown`，不得臆造方向。
- **证据强度**：`evidence_strength` ∈ `stated | inferred | uncertain | conflict`。线宽编码：stated 最粗、inferred 中、uncertain 最细（图例同步说明）。
- 歧义措辞（"可能""提示""相关于"）→ 归 `inferred`/`uncertain`，不画成确定激活。

## 3. 通路方向与区室布局

- 默认流向：`extracellular → membrane → cytosol → nucleus`（`compartments` 顺序即横向列顺序）。
- 受体放 membrane 且 `transmembrane: true`；转录因子入核用 `transport`；胞内级联按上下游从左到右/自上而下排。
- 分支：同一源多个 target 用多条 relation；反馈环：下游指回上游的 relation 单独一条，`evidence` 注明反馈方向。
- 布局可由 Agent 在 entity 上写 `x/y/w/h` 精调；未写则由 `build_svg.py` 确定性自动排布。

## 4. 图例与数据编码

- **图例与图面同源**：图例由 `build_svg.py` 从实际使用的实体类型/关系类型样式表自动生成，Agent 不得手写与之冲突的图例文字。
- 颜色/线型/端点由关系类型决定（见 build_svg.py `REL_STYLES`），线宽编码证据强度（`STRENGTH_FACTOR`）。
- 表达量/显著性/置信度等数值编码：写入 `data_mappings`，每项含 `visual`、`encodes`、`scale` 和 `evidence`（locator+quote）。无来源数值不编码、不标注，记入 `uncertainty`。
- 若需覆盖默认样式，用 entity/relation 的 `style` 字段（fill/stroke/stroke_width/width/height），并在 data_mappings 说明，避免图例失配。

## 5. 证据与自检提示

- 每条结论在写 spec 时就带 `evidence[].locator` + `evidence[].quote`；最终 result.json 的 `evidence_map` 汇总每项（element_ref/kind/locator/quote/claim/confidence）。
- 冲突信息不隐藏：同一指标两来源不一致 → 两者都记，标 `source_conflict`。
- 任何阶段先 `python scripts/main.py skeleton` 落盘，再 `validate-spec`、`build-svg`、`check-result` 循环；阻断项（exit 1）必须清零后才进入导出。
