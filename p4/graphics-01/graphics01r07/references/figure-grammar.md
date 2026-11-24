# figure_spec 图形语法（sci-schematic-editor）

`main.py build` 读取一个 `figure_spec.json`，确定性渲染成**分层可编辑 SVG**。相同 spec 逐字节产生相同 SVG，改数据后重跑即重绘。所有坐标与字号单位均为**毫米（mm）**，SVG 以 `viewBox` 采用 mm、`width/height` 标注 `mm`，便于印刷尺寸精确。

## 1. 顶层字段

```json
{
  "title": "示意标题（可空）",
  "output_file": "figure.svg",
  "font_family": "DejaVu Sans, Arial, sans-serif",
  "canvas": {"width_mm": 180, "height_mm": 120, "margin_mm": 6,
             "background": "#ffffff", "dpi": 300},
  "entities": [], "relations": [], "groups": [],
  "legend": {"items": []}, "annotations": []
}
```

- `canvas.dpi` 仅用于导出元信息（`width_px = round(mm*300/25.4)`），不改变矢量坐标。
- `output_file` 不含路径分隔符；被 `build` 截取 basename 后写入 `/app/output/`。

## 2. 实体 `entities[]`

| 字段 | 说明 | 默认 |
|-|-|-|
| `id` | 唯一图元 ID，写入 provenance 的 `element` | 必填 |
| `label` | 可编辑 `<text>` 内容，**只写材料中真实出现的词** | id |
| `role` | `node`/`process`/`group`/`annotation` | node |
| `shape` | `rect`/`rounded`/`ellipse` | 按 role 推断 |
| `group` | 所属分组 id（对应 `groups[].id`） | 无 |
| `x,y,w,h` | 显式坐标/尺寸（mm）；缺省时确定性自动布局 | 自动 |
| `fill`,`stroke`,`stroke_width` | 样式；颜色须与 legend 一致 | 见下 |
| `font_size`,`pad` | 字号（mm）与内边距 | 3.5 / 1.4 |

- 自动布局：按关系的 `from→to` 做最长路径分层，层内按 `id` 字典序排列；含环也收敛。
- 长标签自动换行（≤3 行），不溢出实体框。

## 3. 关系 `relations[]`

| 字段 | 说明 |
|-|-|
| `from`,`to` | 端点实体 id；方向即箭头方向，**上下游不可倒置** |
| `type` | 见下表；决定箭头/抑制符/线型 |
| `label` | 边标签（可空） |
| `color`,`stroke_width`,`dash`,`marker` | 覆盖默认样式 |

关系类型与图形语义（评判点：抑制≠激活、包含≠并列）：

| type | 端点 | 线型 | 含义 |
|-|-|-|-|
| `activation` | 三角箭头 → | 实线 | 激活/促进 |
| `inhibition` | T 形抑制符 ⊣ | 实线 | 抑制/阻断 |
| `contains` | 无箭头 | 实线 | 包含/隶属 |
| `parallel` | 无箭头 | 虚线 | 并列 |
| `upstream` / `downstream` | 箭头 | 实线 | 上下游 |
| `causal` | 箭头 | 实线 | 因果 |
| `temporal` | 箭头 | 虚线 | 时序先后 |
| `mapping` | 箭头 | 点划线 | 数据/图例映射 |

## 4. 分组 `groups[]`

- `{id, label, x, y, w, h}`；若成员实体存在（`entity.group == id`），自动取成员包围盒 + 3mm 内边距，忽略显式坐标。
- 渲染为虚线圆角框 + 左上角灰标签，表达「包含」边界。

## 5. 图例 `legend.items[]`

`{label, value, color, shape}`；`shape ∈ rect|circle|line|arrow`。图例项应与数据表分类**同源**：先建数据表，再据表生成 legend 与实体配色，保证「图例项、配色、标记与数据分类数目逐项一致」。

## 6. 标注 `annotations[]`

`{text, x, y, font_size, color}`；单位、子图编号（A/B/C）、浓度/时间点等文字放在此层，全部逐字来自材料。

## 7. 图层（可编辑结构保证）

输出 SVG 固定含 6 个 `<g>`：`layer-background`、`layer-groups`、`layer-relations`、`layer-entities`、`layer-legend`、`layer-annotations`。所有文字为真实 `<text>`，可单独选择、改标签不必整张重画；字体族由 `font_family` 统一控制，可替换。

## 8. 数据表驱动重绘

图表类任务将数值写进 `/app/work/` 下的 CSV/JSON（材料原文），`figure_spec.json` 的 legend/entities 由该数据表生成；改数据表后重跑 `main.py build` 即得一致新图。数据表字段：第一行表头，其后为分类列 + 数值列 + 单位列（如 `group,value,unit`）。
