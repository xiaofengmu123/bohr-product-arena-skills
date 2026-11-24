# 中间表示规范（figure_model.json / plan.json / inventory.json）

本 Skill 的核心是把"科学事实"与"绘图决策"分离。三个中间产物按顺序生成，均可用文本编辑器打开、用 `scripts/main.py` 单独重跑。

## 1. inventory.json（材料清单）
`python scripts/main.py inventory` 扫描 `/input` 后产出。

```json
{
  "input_dir": "/input",
  "generated_at": "2026-01-01T00:00:00+08:00",
  "materials": [
    {"path": "materials/method.md", "bytes": 1024, "kind": "text", "lines": 40, "chars": 8120},
    {"path": "figures/apparatus.pdf", "bytes": 300000, "kind": "binary"},
    {"path": "case.json", "bytes": 5000, "kind": "input_manifest"}
  ]
}
```

- `kind`：`text`（可直读文本）/ `binary`（PDF、Office、图片，需平台工具解析）/ `input_manifest`（case.json、task.md）/ `skipped`（超 2MB，记录 `reason`）。
- 用途：告诉 Agent 有哪些真实材料、哪些需要先解析；**不依据文件名假设内容**。

## 2. figure_model.json（科学真相，唯一权威）
Agent 在第 3 步填写（可用 `scripts/main.py template` 生成骨架），第 4 步规范化。SVG 不承载科学判断，所有事实必须先在 model 中成立。

```json
{
  "case_id": "case-xyz",
  "figure_kind": "flow",
  "title": "……",
  "canvas": {"width": 1000, "height": 700, "unit": "px"},
  "font_family": "Arial, 'Noto Sans CJK SC', sans-serif",
  "font_embed": false,
  "entities": [
    {"id": "e1", "label": "反应釜 A", "type": "vessel", "source_ref": "q1", "note": ""}
  ],
  "relations": [
    {"id": "r1", "from": "e1", "to": "e2", "kind": "material",
     "direction": "forward", "line": "solid", "label": "", "source_ref": "q2"}
  ],
  "steps": [
    {"id": "s1", "order": 1, "label": "投料", "kind": "main", "source_ref": "q3", "note": ""}
  ],
  "data_encoding": [
    {"param": "温度", "mapping": "color=#d62728", "target_id": "e1", "source_ref": "q4"}
  ],
  "legend": [{"key": "material", "label": "物料流向", "color": "#1f77b4", "line": "solid"}],
  "evidence": [
    {"id": "q1", "locator": "materials/method.md#L12-L14", "quote": "反应釜 A 通过管路接收原料液"}
  ],
  "uncertainty": [],
  "pending_human": []
}
```

字段约定：
- `entities[*].type`：`vessel / device / instrument / material / stage / component / other`（同类部件用不同 `id` 区分，禁止合并）。
- `relations[*].kind`：`material / energy / info / signal / sequence / step / relation`；颜色映射见 `core_impl.KIND_COLORS`。
- `relations[*].direction`：`forward`（正向实线箭头）/ `backward`（回流，虚线）/ `bidirectional`（双向虚线双箭头）；`line` 可选 `solid|dashed`，缺省由 direction 推断。
- `relations[*].from/to`：必须指向存在的 entity 或 step id，否则 `check` 报 `dangling-relation`（critical）。
- `steps[*].order`：唯一整数，与正文顺序逐条对照；`kind`：`main / parallel / loop / terminate`。
- `evidence[*]`：**硬要求**。`locator` 为材料定位（文件#行/段），`quote` 为逐字原文；`source_ref` 必须命中某个 `evidence.id`，否则 `check` 报 `broken-source-ref`（critical）。

## 3. plan.json（确定性布局）
`python scripts/main.py plan` 从 model 计算，无随机数。

```json
{
  "case_id": "case-xyz",
  "layout_engine": "grid-deterministic-v1",
  "canvas": {"width": 1000, "height": 700, "unit": "px"},
  "font": {"family": "Arial, ...", "embed": false},
  "placements": [{"id": "e1", "x": 100, "y": 200, "w": 140, "h": 46, "kind": "vessel"}],
  "routes": [{"relation_id": "r1", "from_xy": [x, y], "to_xy": [x, y],
              "dashed": false, "bidirectional": false}],
  "legend_entries": [{"kind": "relation", "key": "material", "color": "#1f77b4",
                      "line": "solid", "label": "物料流向", "source_ref": "q2"}],
  "legend_box": {"x": 600, "y": 550, "w": 380, "h": 100}
}
```

布局规则（`grid-deterministic-v1`）：
- 有 `steps` 时：步骤按 `order` 垂直列于左 32% 主序列，实体网格排布于右侧；无 `steps` 时实体全画布网格排布（列数 ≈ √n）。
- 箭头起止点裁剪到节点边框，不穿越节点内部；`backward/bidirectional` 用虚线，`bidirectional` 加反向箭头。
- 图例置于右下，容纳关系类型与 `data_encoding` 色样；画布底部为图例预留 150px。

## 4. SVG 图层契约
`scripts/main.py render` 生成 `/app/output/figure.svg`，分层固定：
`layer-background` → `layer-relations`（先画）→ `layer-entities` → `layer-steps` → `layer-annotations` → `layer-legend`。
每个图元带：`data-entity-id` / `data-relation-id` / `data-step-id` / `data-source-ref` / `data-label-full`（截断前的完整标签），`<metadata>` 注明溯源链。
