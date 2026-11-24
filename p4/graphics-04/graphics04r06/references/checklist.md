# 自检清单与常见失败样例（scix-figure-audit）

## 1. 交付前逐条勾选
- [ ] 已产出 `/app/output/result.json`，含 `case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`change_log` 与六类契约字段。
- [ ] 已产出可编辑分层 SVG（`layer-relations`/`layer-entities`/`layer-steps`/`layer-annotations`/`layer-legend`）。
- [ ] 已产出真实渲染 PNG/PDF 预览；缺渲染工具时已在 `limitations` 说明 `render_status=skipped`。
- [ ] 每个 entity/relation/step/图例均含 `source_ref`，`evidence` 含 `locator` + 逐字 `quote`。
- [ ] 所有 relation 的 `from`/`to` 指向存在的实体或步骤，无断头箭头。
- [ ] 箭头方向与材料流向一致，回流用虚线，无反方向。
- [ ] 图例颜色/线型与关系一致，无"图例与图形不一致"。
- [ ] 数值标注与来源一致，无凭空补齐的部件、管路或数值。
- [ ] 步骤 `order` 与正文顺序逐条对照，编号唯一。
- [ ] 所有节点在画布内，无溢出。
- [ ] 已运行 `python scripts/selftest.py`，critical 全部通过；未通过项已在 `uncertainty`/`limitations` 说明。

## 2. 常见失败样例（须规避）
1. 只给文字描述或单张栅格截图，无 SVG 源文件。
2. 部件名称与材料对不上；把两个同类部件合并成一个。
3. 管路/箭头凭空补齐、方向反向、箭头断头。
4. 步骤编号与正文顺序错位。
5. 图例颜色与图形不一致。
6. 字体未嵌入/缺失，导出后中文变方框。
7. 数值标注无来源或与来源不一致。
8. 画布尺寸不够导致元素溢出被裁剪。
9. result.json 缺 `case_id`/`status`/`sources`/`uncertainty`/`change_log`。
10. 伪造渲染成功、缺失软件状态或评测分数。

## 3. 失败处置
- 同一策略失败两次即换策略；错误信息写入 `change_log` 与 `limitations`。
- 无法判断的部件/数值/来源标注"无法判断"，禁止编造。
- 两个来源对同一指标不一致时，两者都记录并标 `source_conflict`。

## 4. 最小调用示例（示意，非正式案例结果）
```bash
cd /skills/weekly-skill
python scripts/main.py init --case /input/case.json --out /app/output --work /app/work
python scripts/main.py inventory --input /input --work /app/work
# Agent 提取并写入 /app/work/figure_model.json（含 evidence + quote）
python scripts/main.py model --work /app/work
python scripts/main.py plan --work /app/work
python scripts/main.py render --work /app/work --out /app/output
python scripts/main.py check --work /app/work --out /app/output
python scripts/main.py finalize --status complete --work /app/work --out /app/output
```
