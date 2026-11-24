# 自检清单与常见失败处置（figure-model-skill）

交付前逐条勾选；括号内为对应的评分维度。提交前必须运行 `python scripts/selftest.py`
且退出码为 0。

## 1. 交付前检查清单

- [ ] 已产出 `/app/output/result.json`，含 `case_id`、`status`、`sources`、`uncertainty`、
      `pending_human`、`change_log` 与 output_contract 全部字段（实际效果·完整性）。
- [ ] 已产出可编辑分层 SVG，含 `layer-grid / layer-relations / layer-entities /
      layer-annotations / layer-legend` 五层（实际效果·完成度）。
- [ ] 已产出真实渲染 PNG/PDF 预览；缺渲染工具时在 `limitations` 与 `exports.render_status`
      中说明（实际效果·可核验）。
- [ ] 每个 entity / relation / legend / annotation 都带 `source_ref` 与逐字 `quote`（实际效果·可核验）。
- [ ] 所有 relation 的 `from` / `to` 指向存在的实体，无断头箭头（实际效果·正确性）。
- [ ] 箭头方向与材料流向一致；回流/反向用紫色虚线，不擅自反向（实际效果·正确性）。
- [ ] 图例颜色/线型/符号与图中实际渲染一致（实际效果·正确性）。
- [ ] 数值/参数映射（data_encoding）与来源一致，不凭空补齐部件、管路或数值（实际效果·正确性）。
- [ ] 步骤/阶段编号与正文顺序逐条对照（实际效果·正确性）。
- [ ] 所有实体在画布内，无溢出裁剪（可靠体验·运行稳定性）。
- [ ] `validate` 三级校验（契约→IR→SVG）critical 项全部通过；未通过项已写入
      `uncertainty` / `limitations`（可靠体验·异常处理）。
- [ ] 说明、输入约定、图元规范、失败清单齐全（生态贡献·文档完整性）。
- [ ] 脚本仅标准库、相对路径、异常有降级；重复运行结果一致（可靠体验·调用与易用性）。

## 2. 常见失败与规避

1. 只输出文字描述或单张栅格截图，没有可编辑 SVG 源文件。
2. 部件名称/数量与材料对不上，或把两个同类部件合并成一个（必须用 id 区分）。
3. 管路/箭头凭空补齐，连接方向反向，或箭头断头。
4. 步骤/阶段编号与正文顺序错位。
5. 图例颜色、线型与图形不一致。
6. 中文字体缺失导致导出后变方框（用 `font-discovery` 检查，缺失则在 fonts 标注）。
7. 数值标注无来源，或与来源不一致。
8. 画布尺寸不足导致元素溢出被裁剪。
9. result.json 缺 `case_id` / `status` / `sources` / `uncertainty` / `change_log`。
10. 伪造渲染成功、缺失软件状态或评测分数（禁止编造）。

## 3. 失败处置

- 同一策略失败两次即换策略；错误信息写入 `change_log` 与 `limitations`。
- 材料缺失/无法判断的部件、数值或来源，标注"无法判断"并写入 `pending_human`，不补造。
- 空输入目录：退出码 0，写 `status=partial` 的合法 result.json，不抛异常、不编造内容。
- 输出契约若声明了未知键：原样保留并填 `null`/`[]`，不改名、不删除。
