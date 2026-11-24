# IR 字段、图元与确定性布图规范（traceable-figure-skill）

IR 是“材料 → 图稿”的中间表示，写到 `/app/work/ir.json`。布图与 SVG 只依赖 IR，不读原始材料，从而保证可复跑。

## 1. 顶层字段
```json
{
  "meta": {"case_id": "...", "kind": "apparatus|flow|mechanism|route|chart",
           "title": "...", "canvas": {"width": 1200, "height": 900}},
  "entities": [], "relations": [], "data_encoding": [],
  "legend_annotations": [], "sources": [],
  "uncertainty": [], "pending_human": [], "notes": []
}
```

## 2. entities（科学实体）
```json
{"id": "e1", "label": "注射泵", "type": "device|step|material|stage|milestone|node",
 "x": null, "y": null, "source_ref": "materials/paper.md#p3",
 "quote": "泵推动流体进入反应器", "note": ""}
```
- `id` 必填且唯一，作为 relation 的 from/to 端点。
- `x`/`y` 可为 null；null 时由 `build` 确定性布局补全。
- `source_ref` 为 locator（文件+页码/段落）；`quote` 为逐字引用，两者构成证据溯源。

## 3. relations（连接/流向）
```json
{"id": "r1", "from": "e1", "to": "e2", "kind": "flow|material|energy|information|sequence|dependency",
 "direction": "forward|back", "style": "solid|dashed|dotted", "label": "进料",
 "source_ref": "materials/paper.md#p3"}
```
- `from`/`to` 必须指向存在的 entity id，禁止断头。
- `direction:"back"` 或 `style:"dashed|dotted"` 表示回流/反馈，画作下方弧线且不参与前向分层。
- `kind` 决定颜色（稳定调色板），图例 key 应与 kind 对齐，避免“图例与图形不一致”。

## 4. data_encoding（数据映射）
```json
{"id": "d1", "param": "流速", "value": "2 mL/min", "maps_to": "箭头线宽",
 "source_ref": "materials/paper.md#p3"}
```
数值/参数 → 尺寸、颜色、线宽、编号。数值必须来自材料，禁止凭空补齐。

## 5. legend_annotations（图例与标注）
```json
{"key": "flow", "color": "#1f77b4", "label": "物料流向", "source_ref": "..."}
```
`key` 与 relations 的 `kind` 对应；`color` 缺省时按 kind 稳定调色板取色。

## 6. sources / uncertainty / pending_human
- `sources`: `{"id","locator","quote","used_for":["e1","r1"]}`——每个关键结论给 locator + 逐字 quote，并标注用到了哪些图元。
- `uncertainty`: 歧义/冲突项（接口冲突、方向存疑、数值缺失），不隐藏。
- `pending_human`: 需要人工确认的项。

## 7. 确定性布图规则
1. 前向边（非 back/dashed）构成 DAG，用 Kahn 拓扑序 + 最长路径分层；同层按实体在 `entities` 中的文件顺序排序，**不使用随机数**。
2. 坐标 `x = margin + layer*dx`，`y = margin + rank*dy`，每层垂直居中；画布不足时自动扩展到能容纳全部实体。
3. 环内/剩余节点按前驱最大层 +1 确定性回退。
4. SVG 坐标用固定小数格式、元数据 JSON `sort_keys=True`，**不嵌入墙钟时间**，保证同 IR 两次输出逐字节一致。
5. 时间戳只出现在 result.json 的 `runtime`/`change_log.ts`，属易失字段；`selftest.py` 用 canonicalize 剔除后比较科学内容核心。
