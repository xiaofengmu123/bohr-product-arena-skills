# IR 中间表示规范（figure-earlydrop-skill）

IR 是材料与图稿之间的唯一桥梁：先从材料抽取 IR，再布图、生成 SVG、自检。规则一经约定即固定，保证重复运行结果一致。

## 1. 顶层字段

```json
{
  "kind": "apparatus | flowchart | mechanism | route",
  "title": "图题",
  "canvas": {"width": 1200, "height": 900},
  "layout": {"columns": 3},
  "entities": [],
  "relations": [],
  "steps": [],
  "stages": [],
  "data_encoding": [],
  "legend": [],
  "annotations": []
}
```

`kind` 决定布图倾向：`apparatus` 按部件装配关系排布；`flowchart`/`mechanism` 按步骤主序列；`route` 按阶段推进。未指定时默认 `flowchart`。

## 2. 实体 entity

| 字段 | 必填 | 说明 |
|-|-|-|
| `id` | 是 | 唯一标识，稳定可引用（如 `e1`、`pump-a`） |
| `label` | 是 | 图上文字，必须与材料用词一致 |
| `type` | 是 | `start/end/step/process/decision/stage/milestone/vessel/equipment/unit/reactor/pump/compressor/valve/branch/sensor/instrument/material/input/output` 之一；未知给最贴近项并在 note 说明 |
| `source_ref` | 是 | 定位符：`文件:页码/段落`（如 `materials/methods.md#p3`） |
| `quote` | 是 | 材料中的逐字片段，用于核验 |
| `note` | 否 | 同类部件区分、歧义说明 |
| `x/y/w/h` | 否 | 手工坐标；缺省时由 build_svg 确定性自动布局 |

规则：**同类部件不得合并**，名称/数量逐条对照材料；缺部件不给默认值，标 `uncertainty`。

## 3. 关系 relation

| 字段 | 必填 | 说明 |
|-|-|-|
| `id` | 是 | 唯一标识 |
| `from` / `to` | 是 | 必须指向已存在实体 id，禁止断头箭头 |
| `kind` | 是 | `flow/material/energy/info/signal/sequence/return/feedback/cycle/waste` |
| `direction` | 是 | `forward | reverse | bidirectional`；`reverse` 表示材料实际流向与源节点相反 |
| `style` | 否 | `solid | dashed`；回流默认 dashed |
| `label` | 否 | 箭头旁文字（数值、管路名） |
| `source_ref` / `quote` | 是 | 同实体 |

方向规则：物料/能量/信息按材料原文判向；回流、反馈、循环用 dashed 且从下游回指上游；废料出口指向出界或 `waste` 实体。

## 4. 步骤 steps 与阶段 stages

- `steps`：`{"id","order","label","source_ref","quote","condition"}`，`order` 与正文顺序逐条对照；并行支路给相同 order，循环回流由 relation 表达，终止条件写入 `condition`。
- `stages`：`{"id","order","label","goal","inputs","outputs","milestone","source_ref","quote"}`，用于技术路线图，阶段间依赖由 relation 表达。

## 5. 数据编码 data_encoding

把"数值/参数 → 视觉属性"显式写出来，且视觉属性必须真的用在 SVG 中：

```json
{"key": "流速", "value": "10 mL/min", "visual": "线宽 2.4px", "source_ref": "...", "quote": "10 mL/min"}
```

常见映射：数值→线宽/尺寸/颜色深浅/编号；分类→颜色/形状；不映射的数值不得凭空标注。

## 6. 图例 legend

```json
{"key": "flow", "visual": "蓝色实线箭头", "label": "物料流向", "source_ref": "...", "quote": "..."}
```

`key` 必须与 relation 的 `kind` 对应；`visual` 与 build_svg 实际画出的线型/颜色一致，禁止"图例与图形不一致"。

## 7. 布图与确定性

- build_svg.py 纯确定性：同一 IR 输入产出字节一致 SVG（无随机数、无时间戳）。
- 缺坐标时按列表顺序左→右、上→下网格排布，列数取 `layout.columns` 或 3（实体 ≤3 时取实体数）。
- 手工坐标优先；坐标冲突时遵循材料原文，标 `uncertainty`。
- 图元形状映射：`start/end/stage/milestone/material/input/output`→圆角矩形；`step/process/vessel/equipment/unit/reactor`→矩形；`decision/valve/branch`→菱形；`pump/compressor/sensor/instrument`→圆形。
- 颜色约定（kind → 线型）：`flow/material` 蓝实线；`energy` 红实线；`info/signal` 绿虚线；`sequence` 灰实线；`return/feedback/cycle` 紫虚线；`waste` 棕点线。

## 8. 溯源与证据

- 每个关键结论、数值、图元都要有 `source_ref`（locator）+ 逐字 `quote`。
- 冲突信息不隐藏：同一指标两处不一致时都记录并标 `source_conflict`，写入 `uncertainty`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。
