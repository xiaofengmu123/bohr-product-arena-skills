# 图形语法与 figure_spec.json 规范

本文件定义 `scripts/spec_to_svg.py` 接受的中间规范格式。规范是「抽取结论」与「渲染产物」之间的唯一接口：Agent 从材料抽取实体与关系后写入本规范，脚本据此确定性渲染分层 SVG，并依据同一规范生成 `result.json` 的 provenance 与图例映射。改数据 = 改规范 = 重跑脚本，全程无随机、无时间戳，保证同规范两次渲染字节一致。

## 1. 顶层结构

```json
{
  "figure_id": "figure01",
  "title": "机制/流程/结构关系图（可空）",
  "canvas": {"width": 2400, "height": 1400, "margin": 80, "dpi": 300},
  "entities": [],
  "relations": [],
  "legend": null,
  "data": null,
  "annotations": [],
  "subfigures": []
}
```

- `figure_id` 必填，用于生成导出文件名。
- `canvas` 可选，缺省 `width=2400, height=1400, margin=80, dpi=300`（约 8×4.67 英寸 @300dpi）。
- 其余数组允许为空；为空时必须在 `result.json` 的对应字段写明原因，不得静默省略。

## 2. 实体（entities）

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `id` | string | 是 | 图内唯一标识，`[A-Za-z0-9_-]+`；跨图/跨子图不得重复 |
| `label` | string | 是 | 可编辑 `<text>` 的显示文字，必须与材料逐字一致或为其忠实缩写 |
| `kind` | string | 是 | `node`（普通节点）/`group`（分组容器）/`process`（流程步骤）/`annotation`（标注文本） |
| `parent` | string\|null | 否 | 所属分组实体 id；用于表达「包含」层级 |
| `x`,`y`,`w`,`h` | number\|null | 否 | 画布像素坐标与尺寸；`x/y` 缺省时由确定性自动布局填充 |
| `category` | string | 否 | 分类标签，与 legend 的 `category` 字段对应，用于配色映射 |

关系「包含」不额外画连线，而是通过 `parent` 把子实体放进分组容器边界内表达，避免「包含画成并列」。

## 3. 关系（relations）

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `id` | string | 是 | 唯一标识 |
| `from`,`to` | string | 是 | 引用实体 id，必须已存在；`from` 为上游/原因，`to` 为下游/结果 |
| `kind` | string | 是 | 见下表 |
| `label` | string | 否 | 边上文字（激活分子名、浓度、时间点等，须有材料出处） |

关系类型 → 箭头/线型（方向语义，画错即科学错误）：

| kind | 语义 | 端点样式 |
|-|-|-|
| `activation` | 促进/激活 | 实线箭头 → |
| `inhibition` | 抑制/阻断 | 实线平头 ⊣ |
| `upstream` | 上游→下游 | 实线箭头 → |
| `downstream` | 下游←上游（等价上游，可统一用 upstream） | 实线箭头 → |
| `causal` | 因果 | 实线箭头 → |
| `temporal` | 时序/先后 | 实线箭头 →，可加序号 |
| `maps_to` | 数据/图例映射 | 虚线箭头 ⇢ |
| `parallel` | 并列（非有向） | 无箭头细线，仅视觉分组 |

`downstream` 与 `upstream` 语义相同、方向相反，为降低画反风险，规范要求一律写成「上游→下游」的有向边（`from`=上游，`to`=下游）；如需强调下游，改用 `kind="downstream"` 但 `from/to` 仍按上游→下游填写。凡不明确方向的关系，写 `parallel`，不得臆造箭头。

## 4. 图例与数据映射（legend / data）

图例必须与实体、数据同源，避免「图例项与数据表分类对不上、颜色与分类数目不一致」。

```json
"legend": {
  "title": "Legend",
  "items": [
    {"id": "L1", "label": "处理组", "category": "treated", "color": "#1f77b4", "marker": "rect"}
  ]
},
"data": {
  "mapping": [{"legend_id": "L1", "entity_ids": ["A", "B"], "note": "材料表1分类" }]
}
```

- `legend.items[].category` 与实体 `category` 一一对应；同一 `category` 必须使用同一 `color`（由脚本从确定性调色板或显式 `color` 统一派发）。
- `data.mapping` 把图例项回指到实体集，作为「图例↔数据逐项一致」的可核验证据。
- `marker` 取值：`rect`（色块）/`arrow`（激活箭头）/`bar`（抑制平头）/`line`（连线）。
- 若材料提供数据表，颜色数必须等于数据表分类数，不得多画或少画。

## 5. 标注与子图

- `annotations`: `{id, text, x, y, anchor}`；`anchor` 指向实体 id 时自动放在其右上，缺省按 `x/y`。
- `subfigures`: `{id, label, entity_ids}`；`label` 如 `a/b/c` 或 `A/B/C`。子图边界矩形绘制在 `layer-panels` 图层，编号可编辑。

## 6. 确定性约定（必读）

1. 渲染无随机数、无墙钟时间、无字体回退差异：同规范字节 → 同 SVG 字节。
2. 实体/关系/图例按规范内出现顺序遍历；同 id 出现两次属规范错误，脚本报错退出。
3. 自动布局是纯函数：分组按 id 字典序排布，组内成员按规范顺序纵向堆叠，间距固定。
4. 颜色优先取显式 `color`；否则按 `category` 首次出现顺序从固定调色板派发（调色板固定于 `spec_to_svg.py`）。
5. 规范化序列化用 `json.dumps(obj, ensure_ascii=False, sort_keys=False, indent=2)`；确定性校验只对「科学内容核心」取哈希，不含墙钟运行时字段（见 `contract-checklist.md` §5）。

## 7. 与 result.json 的对应

- 每个实体/关系/图例项/标注/子图，都应在 `result.json` 的 `provenance` 有一条记录：`element` 写其 id，`locator` 写材料定位（页/节/行列），`quote` 写逐字片段，`mapping` 写数据/图例映射，`version` 写规范版本号，`confirmed` 为 `true` 仅当有逐字材料支撑。
- 无材料支撑的图元：`confirmed=false`，并在 `uncertainty` 标注「推测」，或干脆不画。
