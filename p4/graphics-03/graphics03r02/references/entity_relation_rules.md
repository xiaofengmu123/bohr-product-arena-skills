# 实体 / 关系 / 布局 / 图例规则

这是把科研材料转成机制规格（`spec.json`）时的判定规则。判定必须基于材料原文，
不得依据文件名或标签臆测；证据不足时降级并写入 `uncertainty`。

## 1. 实体（entities）

| type | 形状 | 书写 | 适用 |
|-|-|-|-|
| `gene` | 椭圆 | **斜体** | 基因/DNA 序列（如 *MYC*、*EGFR*） |
| `protein` | 矩形 | 正体 | 蛋白产物（如 MYC、EGFR） |
| `complex` | 六边形 | 正体 | 多亚基复合物（如 mTORC1、APC/C） |
| `family` | 虚线圆角矩形 | 正体 | 家族/一类成员（如 RAS family） |
| `receptor` | 圆角矩形 + 跨膜竖线 | 正体 | 跨膜受体（`transmembrane:true`） |
| `metabolite` | 圆形 | 正体 | 小分子/代谢物（如 ATP、cAMP） |
| `cell_type` | 圆角矩形 | 正体 | 细胞类型（如 T cell） |
| `compartment` | 背景色带 | 正体 | 亚细胞区室（extracellular/membrane/cytosol/nucleus） |

命名规范（对应评分"实体画对"）：

1. **基因斜体 / 蛋白正体**：同一名称的基因与蛋白不得混用；材料只给一个时按上下文判定，
   并在 `uncertainty` 记录判定依据。
2. **物种前缀**：跨物种材料保留物种前缀（hEGFR/mEGFR），缺省时在 `species` 字段注明，
   不自行假设。
3. **同义名归并**：旧名、别名写入 `aliases`，图上用主名；两材料各用一个名字时，
   `uncertainty` 记录"异名同义"，并在 `sources` 给出两处原文。
4. **复合物 vs 家族成员**：复合物是稳定多亚基实体，家族是"一类成员"；不得把家族名
   画成单个蛋白节点。
5. **区室归属**：每个实体写 `compartment`；受体写 `membrane` + `transmembrane:true`。

## 2. 关系（relations）

| type | 箭头/端点 | 线型 | 语义 |
|-|-|-|-|
| `activation` | 实心三角 → | 实线 | 促进/激活/上调 |
| `inhibition` | 短横杆 ⊣ | 实线 | 抑制/阻断/下调 |
| `binding` | 两端圆点（无箭头） | 虚线 | 结合/相互作用 |
| `modification` | 空心三角 → | 实线 | 磷酸化/泛素化/乙酰化等修饰 |
| `transport` | 双线箭头 › | 点虚线 | 转运/转位/入核 |
| `conversion` | 双平行线 + 实心三角 → | 实线 | 代谢转化/物质转化 |

方向判定规则：

1. **主语→宾语**：`A 激活 B` = A→B activation；`B 被 A 抑制` = A→B inhibition。
2. **修饰方向**：`A 磷酸化 B` = A→B modification，`modifier:"P"`；泛素化用 `Ub`。
3. **转运方向**：`ERK 转位入核` = cytosol ERK → nucleus ERK transport。
4. **跨膜方向**：受体结合配体 = extracellular 配体 → membrane 受体 binding；
   受体活化下游 = membrane 受体 → cytosol 下游 activation。
5. **方向不明**：材料只说"相互作用/相关"→ 写 `uncertainty`（`direction_ambiguous`），
   图上用 binding，不得强行画成激活。

## 3. 布局与拓扑（layout）

- 区室自上而下固定顺序：extracellular → membrane → cytosol → nucleus；
  附加区室（mitochondrion/endosome/lysosome）排在核后，顺序由声明决定。
- 通路主方向自上而下、分支自左向右；反馈环（如负反馈）允许回头边，但不得与主方向混淆。
- 跨膜受体必须横跨 membrane 色带（脚本自动画跨膜竖线）。
- `layout.compartments` 可声明空区室（如为上下文保留 extracellular），
  未声明区室且无实体时不画，避免空色带。
- 实体可用 `x`/`y` 显式定位；缺省时脚本按区室确定性自动排布（可复现）。

## 4. 图例与数据编码（legend / data_mappings）

- 图例由 `build_svg.py` 从**实际使用**的实体类型/关系类型自动生成，保证图例与图面自洽；
  手工写图例说明时，必须逐项核对颜色、线宽、形状、箭头与图面一致。
- `data_mappings` 记录颜色/线宽/形状/大小所编码的数据（表达量、显著性、置信度、时间点），
  每条带 `source.locator` + `quote`；无来源的数值**不得**编码进图面。
- 显著性/置信度等须保留原文表述（如 p<0.05、n=3），不自行换算。

## 5. 证据溯源（硬要求）

- 每个关键实体、每条边、每条数据映射都必须有 `source.locator` + **逐字 quote**。
- `locator` 形如 `materials/paper.md#p3` 或 `input/task.md#L10`；quote 必须逐字抄录，
  不得改写。
- 同一指标两处冲突时，两处都记录并标 `source_conflict`，图面取其一并在 `uncertainty` 说明。
- 材料缺失/无法判断 → `uncertainty` + `pending_human`，图上不画未证实关系。
