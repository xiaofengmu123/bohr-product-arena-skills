# 自检清单与常见失败样例（scix-evidence-figure-skill）

## 1. 交付前逐条勾选

- [ ] 已产出 `/app/output/result.json`，含 `case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`change_log` 与六类契约字段。
- [ ] 已产出可编辑分层 SVG（`layer-relations`/`layer-entities`/`layer-annotations`/`layer-legend`）。
- [ ] 已产出真实渲染 PNG/PDF 预览；缺渲染工具时已在 `limitations` 说明且 `exports.render_status="skipped"`。
- [ ] 每个 entity/relation/annotation/图例均含 `data-source-ref` 与 `data-confidence`。
- [ ] 每个关键图元有 `evidence.locator` + 逐字 `quote`；缺失时已标 `missing`，未补造。
- [ ] 所有 relation 的 `from`/`to` 指向存在的实体，无断头箭头（`scripts/main.py check` 通过）。
- [ ] 箭头方向与材料流向一致，回流用 `return`/`feedback`（虚线），无反。
- [ ] 图例颜色/线型与图形一致，无“图例与图形不一致”。
- [ ] 数值标注与来源一致，无凭空补齐的部件、管路或数值。
- [ ] 步骤编号与正文顺序逐条对照。
- [ ] 所有实体在画布内，无溢出。
- [ ] 已运行 `scripts/selftest.py` 全绿；`check` 的 critical 项全部通过。

## 2. 常见失败样例（须规避）

1. 只给一段文字描述或单张栅格截图，无 SVG 源文件。
2. 部件名称与材料对不上；把两个同类部件合并成一个。
3. 管路/箭头凭空补齐，连接方向反向，或箭头断头。
4. 步骤编号与正文顺序错位。
5. 图例颜色与图形不一致。
6. 字体未嵌入/缺失，导出后中文变方框。
7. 数值标注无来源或与来源不一致。
8. 画布尺寸不够导致元素溢出被裁剪。
9. result.json 缺 `case_id`/`status`/`sources`/`uncertainty`/`change_log`。
10. 伪造渲染成功、缺失软件状态或评测分数。
11. `confidence=low` / `evidence.status=missing` 的图元被当成既定事实，未进 `uncertainty`。

## 3. 失败处置

- 同一策略失败两次即换策略；错误信息写入 `change_log` 与 `limitations`。
- 无法判断的部件/数值/来源标注“无法判断”并进 `pending_human`，禁止编造。
- 空输入、坏 JSON、坏编码均须退出码 0 并写合法 result.json（`insufficient_evidence` / `input_error`），不抛异常。
