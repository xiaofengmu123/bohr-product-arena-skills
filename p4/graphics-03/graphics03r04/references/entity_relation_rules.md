# 实体、关系、布局、图例与证据规则

本文件是抽取与建模时的判定规则，与 `scripts/build_svg.py` 的默认行为一致。核心原则：**图上每一个元素都是一种机制陈述，画错一处等于给出一个错误论断；没有来源就不画，画了就标来源。**

## 1. 实体命名规范

| 类别 | 写法 | SVG 默认样式 | 说明 |
|-|-|-|-|
| 基因 | 斜体 | `italic` | 物种前缀（人 `hEGFR`，鼠 `mEgfr`）按材料保留；无前缀不臆造。 |
| 蛋白 | 正体 | `normal` | 与同源基因区分开，不得混用。 |
| 复合物 | 粗体 | `bold` | 记为 `complex`，成员写入 `synonyms` 或另立实体。 |
| 家族 | 斜体 | `italic` | 记为 `family`，不把"家族名"当"具体成员"。 |
| 细胞类型 | 正体 | `normal`（椭圆） | 记为 `cell_type`。 |
| 亚细胞区室 | 正体 | `normal`（灰框） | 记为 `compartment`，作为布局容器而非普通节点。 |
| 小分子/代谢物 | 正体 | `normal` | 记为 `small_molecule`。 |

- 同义名/旧名归并到 `synonyms`，`label` 用材料主名或权威名；存在同名异义时必须在 `uncertainty` 说明，不得静默合并。
- 复合物与家族成员：图面上标注复合物名，其组成写入 `synonyms`；材料只给了家族名而未给具体成员时，节点写家族名，`type=family`，不冒充具体成员。

## 2. 关系语义映射

| 材料措辞 | type | 端点样式 | 颜色 |
|-|-|-|-|
| 促进/激活/诱导/上调/phosphorylates→活化 | `activation` | 实心箭头 → | 深灰黑 |
| 抑制/阻断/下调/负调控 | `inhibition` | 平头 bar ⊣ | 红 |
| 结合/相互作用/募集/形成复合物 | `binding` | 圆点 ● | 深蓝 |
| 磷酸化/泛素化/甲基化/乙酰化（修饰） | `modification` | 空心圆 ○ | 紫 |
| 转运/转位/分泌/入核/出核 | `transport` | 空心箭头 ⇢（虚线） | 绿 |
| 转化/催化/代谢产物生成 | `conversion` | 实心箭头 → | 橙棕 |
| 语义不明 | `unknown` | 灰箭头（虚线） | 灰 |

- 方向：`source→target` 一律按生物流向书写（配体→受体→胞内→核内；反馈环为下游→上游）。`direction=unknown` 表示流向不明，必须进 `uncertainty`。
- 修饰关系若同时改变了活性（磷酸化激活），优先按材料措辞判定；若材料只说"磷酸化"未说活性后果，用 `modification` 并加 `modifier:"phosphorylation"`，不得自动升格为 activation。
- 抑制的 bar 端点画在目标端；双向关系 `direction=bidirectional` 两端都加端点。

## 3. 通路方向与区室布局

- 区室层级（自上而下）：`extracellular → membrane → cytosol → nucleus`；细胞器归 `organelle`；无法判定归 `unknown`。
- 跨膜受体：`compartment="membrane"`、`transmembrane:true`，画在膜条带上；不得把受体画进胞质或核内。
- 入核转录因子：膜/胞质实体通过 `transport` 边进入 `nucleus` 实体；核内转录调控用 `activation/inhibition` 指向核内靶基因。
- 分支：同一上游可多条出边；反馈环：下游实体指回上游实体（source 在下游、target 在上游），箭头照画、语义照标。
- 区室归一别名：`pm/plasma_membrane/cell_membrane→membrane`；`cytoplasm/cytosol→cytosol`；`nuclear/nucleoplasm→nucleus`；`ec/extracellular_space→extracellular`；线粒体/内质网/内体/溶酶体→`organelle`。

## 4. 图例与数据编码

- 图例由 `build_svg.py` 依据 spec 中实际出现的 relation/entity type 统一生成，保证图例与图面自洽；不要手写与图面矛盾的图例。
- 颜色编码关系类型、形状编码实体类别、线型（实线/虚线）编码证据强度或 transport。
- `data_mappings` 把数值（表达量/显著性/置信度/时间点）绑定到元素；**任何数值必须有 `evidence`（locator + 逐字 quote），无来源不标注数值**。

## 5. 证据分级与溯源（本版核心）

| 等级 | 含义 | 图面处理 | 约束 |
|-|-|-|-|
| `direct` | 材料逐字匹配 | 实线、常规样式 | 必须有 quote |
| `inferred` | 上下文推断 | 关系用虚线（非 transport）、实体常规 | 必须有 quote + 在 uncertainty 简述推断依据 |
| `missing` | 未报道/无法判定 | 灰虚线边框 + 问号，或只入 uncertainty | 不得作为肯定结论 |

- 每条 evidence 含 `locator`（如 `materials/paper.md#p3`、`materials/fig1.png`、`/input/case.json#entities.0`）与 `quote`（材料中的逐字片段，非改写）。
- 冲突信息不隐藏：同一指标两个来源不一致时，两个来源都记录，并加 `uncertainty` 条目标 `issue="source_conflict"`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 6. 降级与异常处置

| 场景 | 处置 |
|-|-|
| 材料缺失/无法判定 | 元素标 `evidence_status=missing` 或不显示，写入 `uncertainty`，`limitations` 注明。 |
| 同名基因与蛋白歧义 | 按上下文证据判定；证据不足列警告并在 uncertainty 说明，pending_human 提问。 |
| 输入数据与图示矛盾 | 不强行画；记录 `source_conflict` 到 uncertainty，pending_human 请人工裁决。 |
| 导出工具缺失 | 只交付 SVG，`export_artifacts` 仅留真实生成格式，`degradations` 记录缺失项。 |
| 字体缺失 | SVG 用可替换 font-family 栈（见下）；导出时如实记录 `font_check`。 |
| 图面元素过多 | 分图层/分区室，或拆分子图；说明拆分依据，保证可读。 |
| 时间/请求不足 | 先落盘骨架，再增量；超时即停，保留当前 result.json。 |

- 字体栈：`font-family="Arial, 'Helvetica Neue', 'Noto Sans', 'DejaVu Sans', sans-serif"`，中文标签追加 `'Noto Sans CJK SC', 'Microsoft YaHei'`。
- 任何情况不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 7. spec.json 最小结构（供 Agent 填写）

```json
{
  "case_id": "case-xxx",
  "title": "示意：某信号通路",
  "entities": [
    {"id": "e1", "label": "EGFR", "type": "protein", "compartment": "membrane",
     "transmembrane": true, "evidence_status": "direct",
     "evidence": [{"locator": "materials/paper.md#p3", "quote": "EGFR ..."}]}
  ],
  "relations": [
    {"id": "r1", "source": "e1", "target": "e2", "type": "activation",
     "direction": "forward", "evidence_status": "direct",
     "evidence": [{"locator": "materials/paper.md#p4", "quote": "..."}]}
  ],
  "legend": [],
  "data_mappings": []
}
```

字段含义与允许值见 `references/output_contract.md` 第 4–6 节；`legend` 可留空由 build_svg.py 自动生成。
