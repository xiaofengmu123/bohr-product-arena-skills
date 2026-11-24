# 自检清单与常见失败样例（scix-figure-contract-skill）

## 1. 交付前逐条勾选

- [ ] 已产出 `/app/output/result.json`，含 `case_id`、`status`、`title`、`figure_kind` 与全部契约字段。
- [ ] `result.json` 字段名与 `case.json` 的 `output_contract` **逐一对齐**（运行 `python scripts/selftest.py` 核验 contract_align）。
- [ ] 已产出可编辑分层 SVG（`layer-relations`/`layer-entities`/`layer-annotations`/`layer-legend`）。
- [ ] 已产出真实渲染 PNG/PDF 预览；缺渲染工具时已在 `limitations` 说明，不伪造成功。
- [ ] 每个 entity/relation/annotation/图例/data_encoding 均含 `source_ref`；能引用原文的一律给逐字 `quote`。
- [ ] `sources` 为去重证据表，每条含 locator + quote；`<metadata>` 含同一映射。
- [ ] 所有 relation 的 `from`/`to` 指向存在的实体，无断头箭头。
- [ ] 箭头方向与材料流向一致；回流/废料用 dashed；无反向。
- [ ] 图例颜色/线型与图形一致，无“图例与图形不一致”。
- [ ] 数值标注与来源一致，无凭空补齐的部件、管路或数值。
- [ ] 步骤编号与正文顺序逐条对照。
- [ ] 所有实体在画布内，无溢出。
- [ ] 已运行 `python scripts/main.py validate ...`，critical 项全部通过；未通过项已在 `uncertainty`/`limitations` 说明。
- [ ] `change_log` 记录 init/merge/finalize 每次改动；`runtime` 含开始与结束时间。

## 2. 常见失败样例（须规避）

1. 只给一段文字描述或单张栅格截图，无 SVG 源文件。
2. 部件名称与材料对不上；把两个同类部件合并成一个。
3. 管路/箭头凭空补齐，连接方向反向，或箭头断头。
4. 步骤编号与正文顺序错位。
5. 图例颜色与图形不一致。
6. 字体未嵌入/缺失，导出后中文变方框。
7. 数值标注无来源或与来源不一致。
8. 画布尺寸不够导致元素溢出被裁剪。
9. `result.json` 缺 `case_id`/`status`/`sources`/`uncertainty`/`change_log`。
10. `result.json` 漏掉 `output_contract` 声明的字段，或擅自改名。
11. 只有 `source_ref` 无逐字 `quote`，复核者无法定位原文。
12. 伪造渲染成功、缺失软件状态或评测分数。

## 3. 失败处置

- 同一策略失败两次即换策略；错误信息写入 `change_log` 与 `limitations`。
- 无法判断的部件/数值/来源标注“无法判断”，禁止编造。
- 渲染失败写 `exports.render_status="skipped"`，只交付 SVG 并在 `limitations` 说明缺 PNG/PDF。
- 中文缺字体时用 `font-discovery` 查字体；仍缺则降级英文标签并标注 `fonts`。
