# result.json 规范 schema 与 FigureIR 字段

本文档在案例 `output_contract` 缺省时作为规范 schema；若案例自带契约，则以契约为准，
并保留全部未知键。

## result.json 顶层

```json
{
  "case_id": "string",
  "status": "completed | partial | failed",
  "graphical_abstract": {
    "question": "string | null",
    "method": "string | null",
    "results": "string | null",
    "conclusion": "string | null"
  },
  "editable_source": {
    "path": "outputs/fig_<case_id>.svg",
    "layers": ["layer-background", "layer-panels", "layer-labels"],
    "text_editable": true,
    "renderer": "native-svg"
  },
  "exports": [
    {"format": "png", "path": "...", "renderer": "...", "status": "rendered | preview_not_rendered"},
    {"format": "pdf", "path": "...", "renderer": "...", "status": "rendered | preview_not_rendered"}
  ],
  "entities": [{"id", "label", "kind", "source", "panel", "role"}],
  "relations": [{"id", "from", "to", "kind", "direction", "source"}],
  "data_mapping": [{"id", "entity_id", "channel", "value", "unit", "scale", "source"}],
  "legend": [{"channel", "meaning", "source"}],
  "sources": [{"id", "locator", "quote", "kind"}],
  "uncertainty": [{"id", "item", "detail", "level"}],
  "pending_human": ["string"],
  "change_log": [{"version", "time", "note"}],
  "limitations": ["string"],
  "runtime": {"seconds": 0, "script_version": "1.1.0"},
  "artifacts": [{"path", "kind", "note"}]
}
```

- `status`：全部关键图面可核验且导出成功为 `completed`；存在缺口但主产物可用为
  `partial`；无法产出主产物为 `failed`。
- `sources` 是图元溯源统一入口；`entities/relations/data_mapping/legend` 中的 `source`
  字段指向 `sources[].id`。
- `exports[].status` 只有 `rendered | preview_not_rendered`；无渲染器时必须后者。

## FigureIR（fig_ir.json）

```json
{
  "schema": "figure-ir",
  "version": 1,
  "case_id": "string",
  "title": "string | null",
  "canvas": {"width": 1000, "height": 700, "unit": "px"},
  "reading_order": ["question", "method", "results", "conclusion"],
  "panels": [
    {"id": "question", "title": "...", "kind": "text", "x": 0, "y": 0, "w": 250, "h": 700,
     "blocks": [{"id", "text", "kind", "source"}]}
  ],
  "entities": [{"id", "label", "kind", "panel", "source", "shape", "role"}],
  "relations": [{"id", "from", "to", "kind", "direction", "label", "source"}],
  "data_mapping": [{"id", "entity_id", "channel", "value", "unit", "scale", "source"}],
  "legend": [{"channel", "meaning", "source"}],
  "evidence": [{"id", "locator", "quote", "kind"}]
}
```

字段规则：

- `reading_order` 固定四要素顺序 `question → method → results → conclusion`；缺少某要素
  时对应面板 `blocks` 为 `[]`，`title` 保留。
- `panels[].x/y/w/h` 由确定性布局引擎按顺序网格计算，坐标以画布像素计；LLM 不要手工
  设绝对坐标，除非材料给出比例尺等硬约束。
- `relations[].direction` 取 `forward | backward | undirected | none`；来源句若写
  “抑制/促进”等方向，必须与箭头方向一致。
- `data_mapping[].channel` 取 `color | length | area | brightness | symbol`，并给出
  `unit` 与 `scale`（若有）。
- `evidence[].quote` 必须是来源材料的逐字片段，`locator` 指向可定位的段落/键。
