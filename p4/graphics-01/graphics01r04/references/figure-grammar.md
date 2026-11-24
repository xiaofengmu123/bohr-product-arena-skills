# figure_spec.json 图面语法规范

`main.py --spec <figure_spec.json>` 读取本规范定义的 JSON，产出分层 SVG 与导出件。所有字段都可选缺省，但**证据字段（`source_ref`、`quote`、`confidence`）不可省略**，否则自检将报错并把元素标为待复核。

## 1. 顶层结构

```json
{
  "figure": { "title": "", "canvas": {}, "style": {} },
  "entities": [ ... ],
  "relations": [ ... ],
  "legends": [ ... ],
  "annotations": [ ... ],
  "sources": [ ... ]
}
```

## 2. figure.canvas

```json
{"width_px": 2400, "height_px": 1400, "dpi": 300, "font_size_px": 16,
 "font_family": "Arial, Helvetica, sans-serif", "arrow_color": "#444444", "tbar_color": "#444444"}
```

- `width_px`/`height_px`：画布尺寸，决定 SVG viewBox 与导出件尺寸。
- `dpi`：导出件标注分辨率；纯标准库 PDF 兜底按 72/dpi 换算 points。
- `font_size_px`：基准字号；`font_family` 可替换（SVG 文字可编辑）。

## 3. entities（实体）

```json
{"id": "e1", "label": "Receptor A", "kind": "node|group|process|annotation",
 "shape": "rect|roundrect|ellipse|text", "x": 0, "y": 0, "w": 160, "h": 56,
 "style": {"fill": "#eef3f9", "stroke": "#1f77b4"},
 "source_ref": "paper.pdf#p3", "quote": "逐字片段",
 "mapping": "实体/数据映射说明", "version": "1", "confidence": "confirmed|inferred|missing"}
```

- `id` 唯一；`label` 只写材料中出现的名称，**禁止脑补基因名/浓度/时间点/单位**。
- `shape=group|container` 或 `kind=group|container` 渲染为虚线边框容器；`shape=text|annotation|note` 仅渲染文字。
- `x/y/w/h` 缺省时由 `auto_layout()` 确定性布局；已给坐标则优先使用。
- `confidence`：`confirmed`（有逐字 quote + locator）/ `inferred`（可说明推导，图面标「推测」）/ `missing`（占位或未画）。

## 4. relations（关系）

```json
{"id": "r1", "from": "e1", "to": "e2", "type": "activation",
 "label": "", "style": {"stroke": "#444444", "stroke_width": 2},
 "source_ref": "", "quote": "", "mapping": "", "confidence": "confirmed"}
```

关系类型 → 图形语法映射（方向与箭头必须与来源一致）：

| type | 图形 | 语义 |
|-|-|-|
| activation / upstream / downstream / causal / temporal / mapping / phosphorylation / promotes | 实线 + 三角箭头 | 激活、上下游、因果、时序、映射 |
| inhibition / negative / repression / suppression / blocks | 实线 + T 形端 | 抑制（**绝不画成激活箭头**） |
| containment / member | 虚线，无箭头（父画成容器框） | 包含、成员（**≠并列**） |
| parallel / sibling / binding / association / interaction | 实线，无箭头 | 并列、结合、互作 |

## 5. legends（图例）

```json
{"id": "l1", "label": "activation", "color": "#1f77b4", "marker": "arrow|tbar|shape",
 "value": "", "source_ref": "", "quote": "", "confidence": "confirmed"}
```

图例必须与 entities/relations 同源：颜色、标记形状与数据表分类**逐项一致**。改数据表 → 改 spec → 重跑 `main.py --spec` 即同步重绘，legend 与 entities 不得分头维护。

## 6. sources / evidence

```json
{"ref": "paper.pdf", "path": "/input/paper.pdf", "locator": "p3", "used_for": "entities e1..e3"}
```

- 每个 `provenance` 条目由 `main.py` 从 entities/relations/legends 的 `source_ref`+`quote` 自动汇总；`source_ref` 与 `sources[].locator` 必须可独立定位。
- `quote` 必须是材料中的**逐字片段**，不得转述、不得补全、不得替换同义词。
- PDF 无文本层时用 OCR/pdftoppm，`locator` 写「OCR 识别，需人工复核」。

## 7. 确定性约定

- 布局与渲染确定性：不引入随机数；同 spec 两次运行产物逐字节一致（除时间戳字段）。
- 重复运行幂等：`result.json` 原子写盘（临时文件 + rename），中途异常不损坏上一版。
- 颜色缺省按 entities 顺序取内置调色板，固定顺序，保证可复现。
