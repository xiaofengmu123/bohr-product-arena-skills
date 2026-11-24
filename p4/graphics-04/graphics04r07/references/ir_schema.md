# IR 规范与图元/布图规则（scix-figure-skill）

IR（中间表示）是"实体—连接—步骤—流向"的机器可读 JSON，介于原始材料与 SVG 之间。
`scripts/main.py run` 会读取 `/app/work/ir.json`（也兼容 `/input/ir.json`），Agent 负责从
材料中抽取并逐条附 `source_ref`；坐标缺省时脚本给出确定性网格/分层布局。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `meta` | object | `title`、`case_id`、`kind`、`layout_mode`（`auto`/`explicit`）、画布参数 `width/height/margin/grid_x/grid_y`（可省略） |
| `entities` | list | 科学实体（部件、单元、步骤、阶段、里程碑、物料/能量/信息节点） |
| `relations` | list | 实体之间的连接与流向 |
| `data_encoding` | list | 数值/参数 → 图面属性（颜色、线宽、符号、编号）的映射 |
| `legend_annotations` | list | 图例与标注映射 |
| `sources` | list | 证据定位，每项含 `locator` 与逐字 `quote` |
| `uncertainty` | list | 不确定与歧义项 |
| `notes` | list | 任意补充说明 |

## 2. entity 字段

```json
{"id": "pump_01", "label": "蠕动泵", "type": "apparatus|step|stage|milestone|material|energy|information|component",
 "x": 120, "y": 80, "source_ref": "materials/methods.md#p3", "confidence": "high|medium|low", "note": "可选"}
```

- `id`：唯一、路径安全；缺省由 `label` 派生（`sanitize_id`）。
- `type` 决定配色与圆角，`step/stage` 用深色描边、浅蓝底。
- `x/y` 缺省时进入 `auto` 布局；`layout_mode="explicit"` 且部分缺坐标时，缺坐标实体仍自动补位。
- 同类部件不得合并：`泵 A` 与 `泵 B` 必须是两个 entity（材料中出现几次就画几次）。
- 名称/数量/接口必须与材料逐字一致；无法判断就写 `confidence="low"` 并进 `uncertainty`，不得补造。

## 3. relation 字段

```json
{"from": "pump_01", "to": "reactor_01", "kind": "material|flow|energy|information|sequence|back|recycle|waste|split|merge",
 "direction": "forward|back|reverse|recycle|feedback|return", "label": "可选", "source_ref": "materials/methods.md#p5"}
```

- `from`/`to` 必须指向存在的 entity，否则自检报 `relation_from_missing` / `relation_to_missing`。
- `direction=back/reverse/recycle/feedback/return` 画虚线；`waste` 用棕色实线；`split/merge` 用空心箭头。
- 箭头方向 = 材料/能量/信息的实际流向，禁止反向；回流必须用 dashed。

## 4. data_encoding 与 legend_annotations

```json
{"field": "流速", "value": "5 mL/min", "encoding": "线宽=2.4px、蓝色箭头", "source_ref": "case.json#params"}
{"key": "material", "label": "物料流向", "color": "#1f77b4", "shape": "line|swatch|box"}
```

- 图例颜色/线型必须与图形一致；校验只做一致性提示（`no_legend`），科学判断仍由 LLM 复核。
- 每个数值标注必须有 `source_ref`，缺来源写 `not_reported` 并进 `uncertainty`。

## 5. 布局与画布

- 默认画布 `1200×900`，`margin=60`，横向格 `240`、纵向格 `130`。
- `auto` 布局：Kahn 拓扑分层 + 层内按 label 字典序排布；环上的反向边画 dashed，节点仍能落盘。
- 画布外实体由 `validate_ir` 报 `canvas_overflow`（error），并在 `pending_human` 提示扩容或改坐标。
- 布局、排序、id 派生全部确定性：相同输入重复运行得到相同 SVG。

## 6. SVG 约定

- 五个命名图层：`layer-canvas`、`layer-relations`、`layer-entities`、`layer-legend`、`layer-annotations`。
- 每个 entity/relation 带 `data-source_ref` 数据属性；SVG 末尾以 `<script id="ir-json">` 内嵌完整 IR，便于复现。
- 文本 `font-family` 降级链：`Noto Sans CJK SC, Microsoft YaHei, PingFang SC, Arial, sans-serif`；缺字体时在 `limitations/fonts` 标注。
