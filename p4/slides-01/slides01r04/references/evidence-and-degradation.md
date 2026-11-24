# 证据溯源、降级与不确定性规则（slides-01 变体重点）

本 Skill 的本版侧重点是**降级与不确定性显式化**：缺数据不编造，限制与不确定状态要写进
`result.json` 的 `limitations` / `uncertainty` / `pending_human`，而不是用猜测补空。

## 1. 证据溯源硬规则

- 每个关键结论、数值、单位、方法名、图元都要能指回材料的**逐字片段**（locator + quote）。
- `quote` 必须是从材料文本层可复制的原句；转写、概括、合并多段 → `status="inferred"`。
- 同一指标两处来源不一致 → 两处都记录，证据标 `status="conflicting"`，并在 `uncertainty` 说明。
- 不虚构 DOI、引用、数值；缺失写 `not_reported` + `pending_human`，或 `limitations`。

定位写法：`materials/paper.pdf#p3`、`materials/paper.md#p4¶2`、`materials/fig1.png`。

## 2. 降级分层（degradations vs limitations vs errors）

| 场景 | 记录位置 | 处置 |
|---|---|---|
| 图表/公式为图片，无法提取文本 | `limitations` + `pending_human` | 不复述其数值，注明“图 X 未提取” |
| PDF 加密或无文本层 | `limitations` + `errors`(若完全读不了) | 用 OCR 或人工，结果标 `inferred` |
| 模板占位符不足 / 字体缺失 / 页数超限 | `limitations` + `degradations` | `report.md` 说明替代版式 |
| 无 LibreOffice/python-pptx 导出 PDF | `exceptions` + `degradations` | 给 SVG+PNG 预览，指向真实文件 |
| PDF 页对象被压缩，页数无法精确统计 | `uncertainty` | `pdfcheck` 标不确定，人工确认 |
| 坏 JSON / 坏编码 / 单文件超限 | `errors` / `degradations` | 继续处理其余材料，不崩溃 |

## 3. uncertainty 与 limitations 的最小结构

```json
{
  "uncertainty": [
    {"about":"图3横坐标单位","reason":"PDF 图注模糊","evidence_status":"inferred","mitigation":"查原文补充材料"}
  ],
  "limitations": [
    {"what":"模板无两栏版式","affects":"第4页方法对比","remedy":"改为上下排列并保留占位符配色"}
  ]
}
```

## 4. 不编造的底线

- 缺数据不是失败，编造才是失败。材料未报告的指标一律 `not_reported`，不填数字。
- 运行结果、评测分数、基准对比只能来自平台真实记录；Skill 不预写正式测试产物或分数。
- 示意输出必须标明“示例”，不冒充正式案例结果。

## 5. 复核与复跑

- `content_map` + `work/draft.json` 的 `evidence_index` 是页级金标准，供他人逐页回溯。
- `scripts/main.py --cmd audit` 检查每页证据缺 locator / verified 缺 quote；`--cmd pdfcheck` 校验页数。
- 同一输入重复运行结果一致：证据状态判定规则固定，布局确定性，`runtime` 记录起止与版本。
