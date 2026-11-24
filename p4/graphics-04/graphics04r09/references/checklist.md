# IR 字段、图元规范与自检清单（figure-ir-skill）

## 1. IR 最小结构（/app/work/ir.json）
```json
{
  "case_id": "来自 case.json",
  "title": "图题",
  "kind": "apparatus|mechanism|flow|route",
  "entities": [
    {"id": "e1", "label": "反应釜", "type": "vessel|unit|step|stage|material|port",
     "x": 80, "y": 100, "source_ref": "materials/paper.md#L12", "quote": "逐字原文"}
  ],
  "relations": [
    {"from": "e1", "to": "e2", "kind": "flow|energy|info|sequence|branch|recycle|waste",
     "direction": "forward|back", "source_ref": "materials/paper.md#L14", "quote": "逐字原文"}
  ],
  "data_encoding": [
    {"key": "温度", "mapping": "120 °C → 节点标红", "source_ref": "materials/paper.md#L15"}
  ],
  "legend_annotations": [
    {"key": "flow", "visual": "#0072B2", "line_style": null, "label": "物料流向"}
  ],
  "sources": [{"path": "materials/paper.md", "locator": "L12-L15"}],
  "uncertainty": [],
  "limitations": []
}
```
- `id` 缺省时脚本按出现顺序分配 `e1,e2,...`；`x/y` 缺省时脚本给确定性网格（apparatus/mechanism）或纵向（flow/route）布局。
- 每个关键结论必须带 `source_ref`（locator）+ `quote`（逐字原文）；quote 只作为 `entities/relations` 条目中的字段，不写入 SVG 文本。

## 2. 图元与配色规范（关系 kind → 视觉映射）
| kind | 颜色 | 线型 | 含义 |
|-|-|-|-|
| flow / material | #0072B2 | 实线 | 物料/能量流向 |
| energy | #E69F00 | 实线 | 能量流向 |
| info / signal | #CC79A7 | 点线 | 信息/信号 |
| sequence | #333333 | 实线 | 先后顺序 |
| branch / split | #D55E00 | 实线 | 分流/支路 |
| recycle / back | #009E73 | 虚线 | 循环/回流 |
| waste | #999999 | 实线 | 废料/出口 |

- 箭头方向由 `direction` 决定：`forward` 箭头在 `to` 端，`back` 箭头在 `from` 端；回流必须用 `recycle`（虚线）。
- 图例缺省时由实际使用的关系类型自动生成，保证"图例与图形一致"。

## 3. 自检清单（交付前逐条勾选）
- [ ] `/app/output/result.json` 含 `case_id`、`status`、`sources`、`uncertainty`、`change_log` 与六类契约字段。
- [ ] 已产出可编辑分层 SVG（background/relations/entities/annotations/legend 五层）。
- [ ] 已产出真实渲染 PNG/PDF 预览；缺渲染工具时 `exports.render_status="skipped"` 并在 `limitations` 说明。
- [ ] 每个 entity/relation/annotation/图例均带 `source_ref`，关键结论带逐字 `quote`。
- [ ] 所有 relation 的 `from`/`to` 指向存在实体，无断头箭头。
- [ ] 箭头方向与材料流向一致，回流用 dashed `recycle`，无反向。
- [ ] 图例颜色/线型与图形一致，无"图例与图形不一致"。
- [ ] 数值标注与来源一致，无凭空补齐的部件、管路或数值。
- [ ] 步骤编号与正文顺序逐条对照。
- [ ] 所有实体在画布内，无溢出。
- [ ] 已运行 `validate`，`fail` 项全部清零；未清零项已在 `uncertainty`/`limitations` 说明。
- [ ] `python scripts/selftest.py` 退出码 0。

## 4. 常见失败样例（须规避）
1. 只给一段文字描述或单张栅格截图，无 SVG 源文件。
2. 部件名称与材料对不上；把两个同类部件合并成一个。
3. 管路/箭头凭空补齐、连接方向反向、箭头断头。
4. 步骤编号与正文顺序错位。
5. 图例颜色与图形不一致。
6. 字体未嵌入/缺失，导出后中文变方框。
7. 数值标注无来源或与来源不一致。
8. 画布尺寸不够导致元素溢出被裁剪。
9. result.json 缺 `case_id`/`status`/`sources`/`uncertainty`/`change_log`。
10. 伪造渲染成功、缺失软件状态或评测分数。

## 5. 失败处置
- 同一策略失败两次即换策略；错误写入 `change_log` 与 `limitations`。
- 无法判断的部件/数值/来源标注"无法判断"，禁止编造。
- 渲染失败只交付 SVG，并在 `exports.render_status` 如实标注，不声称导出成功。
