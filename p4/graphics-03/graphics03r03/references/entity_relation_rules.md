# 实体 / 关系 / 布局 / 图例规则

## 1. 实体命名规范（画对的第一道防线）

| 规则 | 落地方式 |
|---|---|
| 基因斜体、蛋白正体 | `name_type=gene` 由渲染器自动 `font-style: italic`；蛋白 `normal` |
| 物种前缀 | 无法从材料确认物种时写进 `species`，不加猜测性前缀；`species=null` 表示未报告 |
| 同义名/旧名归并 | 合并为同一实体，旧名入 `aliases[]`，图上只用一条规范 `label` |
| 复合物 vs 家族成员 | `complex` 用粗边框矩形；`family` 用虚边框矩形；不得把家族名当单个蛋白画 |
| 细胞类型 / 区室 / 配体 | 分别用 `cell_type`（圆角）、`compartment`、`ligand`（圆）；与蛋白/基因区分 |
| 同名基因与蛋白 | 依据上下文证据判定 name_type；证据不足 → 记录 `uncertainty`，不臆断 |

命名判定必须逐字引用材料（`locator` + `quote`），不能凭标签猜测。

## 2. 关系语义映射（文字 → 图元）

| 材料措辞示例 | `type` | 图元 |
|---|---|---|
| 促进、激活、上调、增强、induces、activates | `activation` | 实线 + 三角箭头（绿） |
| 抑制、阻断、下调、拮抗、inhibits、suppresses | `inhibition` | 实线 + T 形端点（红） |
| 结合、相互作用、募集、binds、recruits、interacts | `binding` / `recruitment` | 无线头实线（灰）/ 点线箭头 |
| 磷酸化、泛素化、甲基化、乙酰化、phosphorylates | `modification` | 箭头 + 修饰标签（紫，中点标 "P"/"Ub" 等） |
| 转运、入核、易位、translocates、import | `transport` | 虚线 + 箭头（蓝） |
| 转化、生成、裂解、converts、cleaves | `conversion` | 短虚线 + 箭头（橙） |
| 表达、转录、expresses、transcribes | `expression` | 长虚线 + 箭头（紫红） |

箭头类型必须可区分；`modifier` 仅在 `modification` 时填充。

## 3. 通路方向与拓扑

- 区室带自上而下：`extracellular → membrane → cytosol → nucleus`（固定顺序）。
- 跨膜受体 `transmembrane=true` 且 `compartment=membrane`，节点压在膜双线上。
- `direction` 语义：`forward` 箭头落在 `to`；`reverse` 箭头落在 `from`（用于负反馈/回环）；
  `bidirectional` 双箭头；`self` 自环（自磷酸化等）。
- 反馈环画为逆流边（下游 → 上游），箭头指向被调控方，布局自证机制方向。
- 上下关系只画有证据的边；不画“看起来合理”的推测边。

## 4. 图例与数据编码（必须自洽）

- 图例与图面由同一张编码表（`RELATION_STYLE` / `NAME_TYPE_STYLE`）派生，天然一致。
- `data_mappings`：`channel` ∈ `fill / stroke_width`；`metric` 指向 `entity.data` 的键；
  `scale` 为 `{min, max}`（数值线性映射）或显式分档。无 scale 的映射不渲染。
- 数值无来源 → 不标注、不进 `data`，写入 `uncertainty`；严禁编造表达量/显著性/置信度。

## 5. 确定性（本版强化点）

- 布局坐标、图例顺序、颜色、SVG 字节全部是 `spec.json` 的纯函数。
- 同输入两次运行产物逐字节一致；`determinism.svg_sha256` 可供复核。
- `change_log` 用相对秒；先落盘骨架，再增量改进，保证中断仍可交付。

## 6. 降级与已知限制

- 材料缺失 → 无法确定的实体/方向/数值入 `uncertainty`，图上不画未证实关系。
- 导出工具缺失 → 仍交付 SVG，`export_artifacts` 只保留真实生成格式，缺口入 `limitations`。
- 字体缺失 → SVG 用可替换 `font-family` 栈；导出时如实记录 `font_check`。
- 当前布局为直线连线：多条同源同靶的边会重叠，应合并语义或用 `layout.order` 调整；
  极端密集图（节点 >30）可能不可读，入 `limitations` 并说明。
