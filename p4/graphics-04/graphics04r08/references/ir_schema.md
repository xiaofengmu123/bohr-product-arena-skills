# IR 中间表示规范（tracefigure-skill）

IR 是“证据中间表示”：从材料抽取后写入 `/app/work/ir.json`，是布图、自检与 result.json 回填的唯一数据源。字段名与 `figure.py`、`main.py` 严格一致。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 与 `/input/case.json` 一致 |
| `figure_kind` | string | 是 | `apparatus` / `flow` / `stream` / `route` / `mechanism` / `chart` |
| `title` | string | 否 | 图标题 |
| `source_ref` | string | 否 | 整图来源定位 |
| `canvas` | object | 否 | `{width, height}`，缺省 960×640 |
| `entities` | list | 是 | 科学实体（六类要素之一） |
| `relations` | list | 是 | 空间/流向/顺序关系（六类要素之一） |
| `data_encoding` | list | 是 | 数据映射（六类要素之一） |
| `legend` | list | 否 | 图例映射；缺省由脚本补默认图例 |
| `annotations` | list | 否 | 额外标注 |
| `steps` | list | 否 | 步骤（流程类图） |
| `stages` | list | 否 | 阶段（技术路线类图） |

## 2. entities 元素

`{id, label, type, count, source_ref, quote, note, x?, y?, w?, h?}`

- `id`：图内唯一；缺省脚本自动生成 `n1/n2/...`。
- `label`：与材料原文一致的中文/英文部件名，禁止改写或泛化。
- `type`：实体类别（material / device / unit / signal / waste / stage…）。
- `count`：同类部件数量（>1 时在图上标注 ×N）。
- `source_ref`：证据定位，形如 `materials/paper.md#p3` 或 `paper.pdf#page4`。
- `quote`：材料中的**逐字片段**（可核验性的核心）。
- `x/y/w/h`：显式坐标；四项齐全时启用显式布局，否则自动布局。

## 3. relations 元素

`{id, from, to, kind, direction, style?, source_ref, quote}`

- `from` / `to`：必须指向存在的 entity/step/stage 的 `id`（自检项 C5）。
- `kind`：`flow`（物料）/ `energy` / `info` / `data` / `contains` / `connects` / `return` / `reflux` / `loop` / `waste` / `bypass`。
- `direction`：`forward`（from→to）/ `backward`（to→from）/ `bidirectional`。
- 线型规则：`return/reflux/loop/backflow/recycle/waste/bypass` 或 `direction=backward` → dashed + 回红色；`data` → dashed + 数据蓝；其余 → 实线 + 深灰。
- 方向必须与材料流向一致，回流用 dashed（自检项 C6）。

## 4. data_encoding 元素

`{key, value, mapping, target, source_ref, quote}`

把材料中的数值/参数映射为图面属性：例如 `key="流速", value="1.2 mL/min", target="线宽=2px", mapping="数值越大线越粗"`。禁止补造无来源数值（自检项 C8）。

## 5. legend 元素

`{key, label, color, line_style, symbol?, source_ref}`

- `line_style`：`solid` / `dashed` / `box`。
- `color`：缺省取调色板（见下）。图例必须与实际图元一致（自检项 C7）。

## 6. 调色板与图元规范（脚本内置，可覆盖）

| key | 颜色 | 用途 |
|---|---|---|
| entity / entity_stroke | `#dbe7f5` / `#2b5f8a` | 实体底 / 描边 |
| step / step_stroke | `#e2efda` / `#4d7c3f` | 步骤 |
| stage / stage_stroke | `#f4e8cf` / `#8a6d2b` | 阶段 |
| flow / backflow / waste / data | `#333333` / `#a33a3a` / `#7a7a7a` / `#5f6fd0` | 主流 / 回流 / 废料 / 数据 |

## 7. 确定性布图规则

- 全部节点给出 `x/y/w/h` → 显式布局（auto-layout=false）。
- 否则按 `figure_kind` 自动布局（auto-layout=true，写入 SVG `<metadata>`）：
  - `flow`/`stream`：步骤纵向主序列，实体列于右侧，阶段列于最右。
  - `route`：阶段横向均分列，实体/步骤排底部。
  - `apparatus`/`mechanism`/`chart`：实体 3 列网格，步骤/阶段排底部。
- 无随机数、固定调色板、按出现顺序稳定遍历 → 同一 IR 生成逐字节一致的 SVG。

## 8. 证据溯源规范

- 每个关键结论/数值/图元都带 `source_ref`（locator）+ `quote`（逐字片段）。
- locator 采用 `文件路径#段落/页码`；PDF 用 `文件.pdf#pageN`。
- 两个来源冲突时都记录并标 `source_conflict`，写入 `uncertainty`；不虚构引用、DOI、数值。
