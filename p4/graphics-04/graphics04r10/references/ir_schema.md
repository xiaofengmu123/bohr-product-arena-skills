# FigureModel 中间表示（IR）规范

本规范定义 `figure-model-skill` 的结构化中间表示（FigureModel）。Agent 先从科研材料抽取
FigureModel 写入 `/app/work/ir.json`，脚本再据此做契约校验、确定性布图、分层 SVG 生成与三级自检。
IR 是"证据优先"的：每个科学断言都要带 `source_ref`（文件 + 页码/段落）与 `quote`（逐字片段），
而不是只给结论。

## 1. 顶层字段

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 与 `/input/case.json` 的 `case_id`（或 `id`）一致 |
| `kind` | string | 是 | `apparatus` / `flowchart` / `route` / `mechanism` / `chart`，未知给 `unknown` |
| `title` | string | 否 | 图题，直接写入 SVG 顶部 |
| `entities` | list | 是 | 科学实体（部件/步骤/阶段/物料/结果） |
| `relations` | list | 否 | 空间、流向、顺序、依赖关系 |
| `data_encoding` | list | 否 | 数值/参数 → 尺寸、颜色、线宽、编号的映射 |
| `legend_annotations` | list | 否 | 图例与标注映射 |
| `steps` | list | 否 | 主序列/并行支路/回流/终止条件（流程图与技术路线用；也可并入 entities） |
| `editable_sources` | list | 否 | 可编辑源文件清单（由 Agent 在生成后回填） |
| `exports` | list | 否 | 导出件清单（由 Agent 在渲染后回填） |
| `sources` | list | 否 | 证据定位汇总 |
| `uncertainty` | list | 否 | 不确定与歧义项 |
| `limitations` | list | 否 | 缺数据/缺软件的如实限制 |
| `pending_human` | list | 否 | 需要人工确认的项 |

`kind` 决定布图：`route`/`flowchart`/`process` 用 3 列"阶段式"阅读顺序；
`apparatus`/`mechanism`/`chart` 用近方形网格。两者都是确定性布局。

## 2. entity 字段

```json
{"id": "pump-1", "label": "蠕动泵", "type": "pump",
 "source_ref": "materials/method.md#p3", "quote": "样品经蠕动泵以 1.0 mL/min 注入",
 "note": "同类部件用 id 区分（pump-1 / pump-2），不得合并"}
```

- `id` 在 entities 内唯一；`label` 必须与材料原文一致，不补造、不合并同类部件。
- `type` 可选，推荐 `material` / `step` / `stage` / `unit` / `port` / `outlet` / `waste` / `energy` / `info`。
- `quote` 为支撑该实体的逐字片段；`source_ref` 为定位。二者缺失只记 warning，缺 id/重复 id 记 critical。

## 3. relation 字段

```json
{"from": "pump-1", "to": "column-1", "kind": "flow",
 "direction": "forward", "source_ref": "materials/method.md#p3",
 "quote": "蠕动泵 → 色谱柱"}
```

- `from` / `to` 必须指向存在的 entity id，否则为 critical（断头连接）。
- `direction` 词汇表：`forward`（默认）、`backward`、`bidirectional`、`none`、
  `up` / `down` / `left` / `right`、`recycle`。`recycle` / `backward` 渲染为紫色虚线，
  用于回流，绝不擅自反向。
- `kind` 可选：`flow` / `spatial` / `order` / `dependency` / `control` / `energy` / `info`。

## 4. data_encoding 字段

```json
{"field": "flow_rate", "mapping": "stroke-width = flow_rate / 0.2",
 "target": "relation.pump-1->column-1", "source_ref": "materials/table1.csv#r2",
 "quote": "1.0 mL/min"}
```

数值/参数到图面视觉属性的映射必须可追溯；映射规则写清"哪个字段 → 哪个图元 → 哪个视觉通道"。

## 5. legend_annotations 字段

```json
{"key": "sample-flow", "text": "样品流向", "color": "#2c6fbb",
 "linetype": "solid", "symbol": "arrow", "source_ref": "materials/legend.md#L1"}
```

`color` / `linetype` / `symbol` 必须与图中实际渲染一致；`source_ref` 说明该图例项的证据来源。

## 6. 确定性约定

- 布局为纯网格，无随机数，重复运行同一 IR 得到字节级一致的 SVG。
- 回流/反向用紫色虚线（`#8e44ad`），正向用深灰（`#2c3e50`）；实体为浅蓝底蓝边。
- 所有渲染元素带 `data-source-ref`；关系还带 `data-from` / `data-to` / `data-direction`，
  供 `validate` 独立复核。
- 字体族统一声明回退链：`DejaVu Sans, Noto Sans CJK SC, Arial, sans-serif`；
  中文字体缺失时由 Agent 用 `font-discovery` 检查并在 `exports.fonts` 如实标注。

## 7. 三级校验顺序（与参考做法不同）

1. `validate_contract`：先核对 result.json 是否含 output_contract 全部字段（缺键 = critical）。
2. `validate_ir`：核对实体唯一性、连接端点存在性、方向词汇表、source_ref 覆盖（断头 = critical）。
3. `validate_svg`：解析 XML，核对图层、实体渲染覆盖、关系端点、箭头标记（缺渲染 = critical）。

校验顺序固定为"契约 → 中间表示 → 渲染"，先保证输出字段合法，再保证科学内容成立，最后保证图件可解析可复核。
