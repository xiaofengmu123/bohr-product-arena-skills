# 图面规范（figure_spec.json）

`spec_to_svg.py` 的输入。所有字段均为标准库可解析的 JSON；未知字段被保留但不影响渲染。核心原则：**图元与来源同源**——每个 node/edge/legend/annotation 都带 `source_ref`（材料定位）与 `quote`（逐字原文），渲染器会把它们写进 SVG 的 `data-source-ref` / `data-quote` 属性。

## 顶层结构

```json
{
  "schema": "editable_relationship_schematic/v1",
  "canvas": {"width": 1200, "height": 900, "title": "Figure 3", "background": "#ffffff",
             "description": ""},
  "nodes": [], "edges": [], "groups": [], "legend": [], "annotations": [],
  "chart": null
}
```

## nodes（实体）

| 字段 | 必需 | 说明 |
|-|-|-|
| `id` | 是 | 唯一标识，同时用作 SVG `<g id>` |
| `label` | 是 | 实体名，可为多行（`\n` 分行） |
| `kind` | 否 | protein/gene/rna/process/compound/drug/cell/complex/condition/structure/unknown，决定默认配色 |
| `group` | 否 | 所属分组 id，与 `groups[].nodes` 二选一或同时给出 |
| `shape` | 否 | rect/rounded/ellipse/pill/hexagon，默认 rounded |
| `x,y,w,h` | 否 | 显式坐标；全部省略时用确定性分层自动布局 |
| `fill,stroke,text_color,font_size,sublabel` | 否 | 样式覆盖 |
| `source_ref,quote` | 否 | 溯源（**强烈建议**） |

默认配色（可复用，被 spec 显式值覆盖）：

| kind | fill | stroke |
|-|-|-|
| protein | `#dbeafe` | `#1e3a8a` |
| gene | `#fce7f3` | `#9d174d` |
| rna | `#fef3c7` | `#92400e` |
| process | `#d1fae5` | `#065f46` |
| compound/drug | `#fef9c3`/`#fee2e2` | `#854d0e`/`#991b1b` |
| cell | `#fee2e2` | `#991b1b` |
| complex | `#e9d5ff` | `#6b21a8` |
| condition | `#e0e7ff` | `#3730a3` |

## edges（关系）

| 字段 | 必需 | 说明 |
|-|-|-|
| `id` | 是 | 唯一标识 |
| `from`,`to` | 是 | 指向 node id |
| `kind` | 是 | activation/inhibition/binding/phosphorylation/repression/association/dissociation/contains/parallel/upstream/downstream/causal/translocation/transport/flow/temporal/interaction |
| `label,color,style` | 否 | 边标注、颜色、solid/dashed |
| `source_ref,quote` | 否 | 溯源 |

箭头约定（方向语义，写错直接判错）：

- `activation`/`phosphorylation`/`upstream`/`downstream`/`causal`/`translocation`/`transport`/`flow`/`temporal` → **实心箭头**。
- `inhibition`/`repression` → **横杠（T 头）**，与激活区分。
- `binding`/`association` → **圆点**。
- `interaction`/`dissociation`/`contains`/`parallel` → 无线头。
- 反向关系不要用 `from/to` 对调掩盖，应改为正确的 `kind` 或新边；不确定方向写进 `uncertainty`，不要猜测。

## groups（层级/包含）

| 字段 | 说明 |
|-|-|
| `id,label` | 分组标识与标题 |
| `nodes` | 成员 node id 列表；**包含关系由成员列表决定**，分组框按成员自动拟合 |
| `x,y,w,h` | 可选显式边界，缺省自动拟合成员 |
| `fill,stroke` | 默认 `#f8fafc` / `#94a3b8` |

包含 vs 并列：把节点放进同一 `group.nodes` 表示包含；并列节点放在不同 group 或都不放。画错的根因几乎都是把并列节点塞进同一 group。

## legend（图例）与数据同源

```json
{"id": "lg1", "label": "Activation", "marker": "arrow|tee|swatch",
 "color": "#166534", "source_ref": "paper.pdf#p4", "quote": ""}
```

- 图例必须与 `edges`/`nodes`/`chart.series` 的实际类型和颜色一致；改数据后重跑 `spec_to_svg.py` 即同步更新。
- `chart` 存在时，渲染器会**自动从 `chart.series` 派生缺失的图例项**（legend 与数据同源），无需手工重复维护。

## annotations（标注）

`{"id","text","x","y","anchor":"start|middle|end","font_size","color","source_ref","quote"}`。用于浓度、时间点、单位、子图编号（如 `(a)`、`10 μM`）。数值与单位必须来自材料原文，标注带 `quote` 才能复核。

## chart（数据图，可选）

```json
{"type": "bar|point", "x_label": "Treatment", "y_label": "Viability", "unit": "%",
 "title": "", "source_ref": "data.csv",
 "categories": ["Control", "1 μM", "10 μM"],
 "series": [
   {"name": "24h", "values": [100, 82, 51]},
   {"name": "48h", "values": [100, 70, 33]}
 ]}
```

- 本渲染器只**按已给数值绘图**，不做统计计算；数值必须来自实际材料。
- 柱/点高按 0–100（百分比）或所有 series 的最大值归一化；图例由 `series` 派生，保证分类与配色一一对应。

## 布局与确定性

- 全部节点给 `x/y` 时按坐标渲染；否则按边构成 DAG 的最长路径分层，层内堆叠（含环时忽略环边仅用于分层），结果可复现。
- 同输入同输出：无随机数、固定色板、固定参数。修改数据只需改 spec 重跑。
