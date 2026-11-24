# 输出契约与中间产物规范

本文件定义三份 JSON 的结构。脚本 `main.py` 与 `core_impl.py` 以这些结构为准；
`SKILL.md` 的调用 Agent 按本规范填写/校验。所有路径均为绝对路径（运行时为
`/app/work/...` 与 `/app/output/...`）。

## 1. result.json（最终交付，写入 /app/output/result.json）

```json
{
  "case_id": "取自 case.json",
  "status": "completed | partial | failed",
  "graphical_abstract": {"question": null, "method": null,
                         "results": null, "conclusion": null},
  "editable_source": {"path": "/app/output/fig_<case_id>.svg",
                      "format": "svg", "layers": ["layer-*"],
                      "text_editable": true, "checksum": "plan 摘要"},
  "exports": {"png": {"path": null, "renderer": null,
                      "status": "rendered | preview_not_rendered"},
              "pdf": {"path": null, "renderer": null,
                      "status": "rendered | preview_not_rendered"}},
  "entities": [], "relations": [], "data_mapping": {}, "legend": [],
  "plan_path": "/app/work/plan.json",
  "figure_model_path": "/app/work/figure_model.json",
  "sources": [], "uncertainty": [], "limitations": [],
  "pending_human": [], "change_log": [],
  "runtime": {"started_at": "", "finished_at": "", "seconds": null,
              "renderer": null, "version": "1.1.0"}
}
```

要点：

- `case_id` 必填；`case.json` 若含 `output_contract`（dict 或 list），其每个键必须
  原样出现在 result.json，缺值给 `null`/`[]`，不改名、不删键。
- `status` 恰为三选一。材料不足或存在未解决缺陷时用 `partial`，输入缺失用 `failed`。
- `sources` 每项：`{"id", "locator", "quote", "file"}`；`quote` 必须为材料中逐字片段。
- `uncertainty` / `pending_human` 记录歧义、缺数值、需人工确认项，不允许编造。

## 2. plan.json（语义意图层，Agent 手工可编辑）

```json
{
  "case_id": "", "figure_type": "graphical_abstract",
  "title": "", "seed": 20261123,
  "canvas": {"width": 1600, "height": 1000, "unit": "px"},
  "reading_order": ["question", "method", "results", "conclusion"],
  "graphical_abstract": {"question": "", "method": "", "results": "", "conclusion": ""},
  "panels": [{"id": "p_question", "role": "question", "label": "研究问题", "order": 1}],
  "entities": [{"id": "e1", "label": "因子A", "kind": "molecule",
                "panel": "p_question", "fill": "#4C78A8", "sources": ["s1"]}],
  "relations": [{"id": "r1", "from": "e1", "to": "e2", "kind": "activates",
                 "directed": true, "label": "激活", "sources": ["s1"]}],
  "data": [{"id": "d1", "label": "产率", "value": 3.2, "unit": "%",
            "encoding": "bar", "sources": ["s1"]}],
  "legend": [{"id": "l1", "channel": "color", "label": "分组A",
              "color": "#4C78A8", "sources": ["s1"]}],
  "constraints": {"margin": 24, "pad": 16, "font_min_px": 10,
                  "title_font_px": 26, "entity_h": 46},
  "sources": [{"id": "s1", "locator": "materials/paper.pdf#p3",
               "quote": "……", "file": "materials/paper.pdf"}],
  "uncertainty": [], "limitations": [], "pending_human": [],
  "change_log": [], "final_status": null
}
```

`kind` 取值建议：`cell / molecule / material / device / sample / process / group`；
`encoding` 取值：`bar | length | color | dot`。`final_status` 可覆盖脚本自动判定的
status（定稿时用），不填则由脚本按校验结果判定。

## 3. figure_model.json（解析几何层，脚本确定性生成）

```json
{
  "version": "1.1.0", "case_id": "", "figure_type": "",
  "seed": 20261123, "canvas": {"width": 1600, "height": 1000, "unit": "px"},
  "checksum": "plan 的 sha256 前 16 位（剔除 change_log）",
  "layers": ["layer-background", "layer-panels", "layer-entities",
             "layer-relations", "layer-data", "layer-legend",
             "layer-title", "layer-footer"],
  "items": [{"id": "ent_e1", "type": "rect", "layer": "layer-entities",
             "x": 0, "y": 0, "w": 0, "h": 0, "fill": "#...",
             "source_ref": ["s1"], "data_ref": null, "channel": null}],
  "entity_count": 0, "relation_count": 0,
  "data_mapping": {"d1": {"channel": "length", "visual_item": "data_d1_bar",
                          "value": 3.2, "unit": "%", "source": ["s1"]}},
  "sources": []
}
```

`items[].source_ref` 指回 `sources[].id`；`data_mapping` 把每个数据项指回具体图元，
是"数值→视觉通道→来源"的可复核链条。相同 plan（同 seed）→ 相同 checksum → 相同 items
→ 字节一致 SVG。
