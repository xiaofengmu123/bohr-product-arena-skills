# 实体/关系/布局/图例规则与清单

本文件是把科研材料翻译成 `spec.json` 的判定规则，也是自检清单的依据。
脚本 `scripts/main.py build` 会按同一套规则校验并归一化 `spec.json`。

## 1. 实体与命名规范

- **基因**：斜体（`display.italic=true`），物种前缀保留（如 `Homo sapiens EGFR`）；**蛋白**正体，不加物种斜体前缀。
- **同义名归并**：同一实体只建一个 `id`，旧名/别名放 `aliases`（如 EGFR → `["HER1","ERBB1"]`），图上只画主名。
- **复合物 vs 家族 vs 成员**：复合物用 `complex`，家族用 `family`（虚线边框），成员用 `gene/protein`，三者不得混用。
- **细胞类型 vs 亚细胞区室**：细胞类型是 `cell_type`（实体），区室是 `compartment`（背景带，不是实体）。
- `type` 取值：`gene|protein|complex|family|cell_type|small_molecule|other`。
- 证据：每个实体必须有 `sources[].locator` + **逐字 `quote`**；无法定名或消歧时写入 `uncertainty`，不臆造名称。

## 2. 关系语义 → 箭头类型

| 文字证据 | `type` | 端点/样式 |
|---|---|---|
| 促进/上调/激活/诱导 | `activation` | 实线 + 实心箭头（红 #C62828） |
| 抑制/下调/阻断/拮抗 | `inhibition` | 实线 + T 形杆（蓝 #1565C0） |
| 结合/互作/形成复合物 | `binding` | 线 + 两端圆点（紫 #6A1B9A） |
| 磷酸化/泛素化/乙酰化/甲基化 | `modification` | 虚线 + 箭头 + 修饰标注 P/Ub/Ac/Me（橙 #EF6C00） |
| 转运/入核/分泌/易位 | `transport` | 空心箭头（绿 #2E7D32） |
| 转化/生成/代谢为 | `conversion` | 双头箭头（青 #00695C） |
| 募集/招募 | `recruitment` | 虚线 + 箭头（棕 #8D6E63） |
| 语义不明 | `unknown` | 点线箭头 + 记入 `uncertainty` |

- `direction`：默认 `forward`；反向证据才写 `reverse`；双向写 `bidirectional`。
- `evidence`：`reported`（材料明说）/ `inferred`（上下文推断）/ `uncertain`（证据不足）。
- 反馈环：目标排名不晚于源的边由布局引擎自动画成左侧回环（`data-feedback`），无需手工指定坐标。

## 3. 通路方向与区室布局

- 默认四带：`extracellular → membrane → cytosol → nucleus`（自上而下）。
- 跨膜受体：`transmembrane=true` 且 `compartment="membrane"`（画在膜带上）。
- 布局由 `scripts/main.py build` 自动计算：实体按关系做最长路径排名，同区室同排名纵向堆叠；线性、分支、反馈环、多区室四种拓扑均支持。
- 若自动布局不理想，可在 `spec.svg` 或实体上给可选坐标覆盖（不推荐默认使用）。

## 4. 图例与数据编码

- 图例**由渲染器从实际使用的关系类型/实体类型/数据映射推导**，不手工另写，保证图面=图例。
- 支持的编码：`node_fill`（颜色→分类，`cat=#hex`）、`node_size`（大小→数值，`min=…,max=…`）、`edge_width`（线宽→数值）。
- 每个 `data_mappings` 项必须有 `sources`（数值来源）；无来源数值**不标注**，写入 `uncertainty`。

## 5. 自检清单（交付前逐条勾选，对应评分维度）

- [ ] 已读 `/input/case.json` + `task.md` + **实际材料文件**（PDF 用 pdf-text-tool/pdf-render-tool，不臆测文件名）
- [ ] 实体命名：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族/成员不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion/recruitment 可区分且证据匹配
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环走向正确
- [ ] 图例与实际配色/线宽/形状/箭头自洽（渲染器自动推导）
- [ ] 数据编码（表达量/显著性/置信度）均有 `source` 与逐字 quote
- [ ] `editable_source_files` 为分层可编辑 SVG（≥2 命名图层）；`export_artifacts` 含真实 PNG/PDF（缺则写 limitations）
- [ ] `sources` 覆盖关键实体/边；`uncertainty`/`pending_human` 记录歧义与缺口
- [ ] `change_log` 首条为 skeleton，后续增量；`status` 与 `limitations` 如实
- [ ] `scripts/main.py check` 退出码 0 或仅 2（警告已说明）；`scripts/selftest.py` 退出码 0
