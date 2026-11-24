# 自检清单与失败样例（tracefigure-skill）

## 1. 评分维度 → 自检项映射

| 评分维度 | 分值 | 自检项（可勾选） |
|---|---|---|
| 实际效果·完成度 | 15 | 交付分层 SVG + 真实 PNG/PDF，而非文字/截图 |
| 实际效果·正确性 | 15 | 部件名/数量/接口、上下游顺序、箭头方向、数值与来源一致 |
| 实际效果·完整性 | 5 | 六类要素（entities/relations/data_encoding/legend_annotations/editable_sources/exports）齐全 |
| 实际效果·可核验 | 5 | SVG 含 data-source-ref；result.json 含 sources/checks |
| 使用增益 | 25 | 前 3 分钟落盘骨架；IR→布图→自检减少返工；selftest 可验证 |
| 创新增益 | 15 | IR 中间表示 + 布图/编号/图例规则沉淀于 ir_schema.md |
| 可靠体验 | 10 | 标准库、相对路径、缺参/坏 JSON/空输入不崩溃、重复运行一致 |
| 生态贡献 | 10 | SKILL.md/references/最小示例齐备，他人可复用 |

## 2. 交付前逐条勾选

- [ ] 已产出 `/app/output/result.json`，含 `case_id`、`status`、`sources`、`uncertainty`、`change_log` 与六类契约字段。
- [ ] 已产出可编辑分层 SVG（layer-relations / layer-entities / layer-steps / layer-stages / layer-data / layer-annotations / layer-legend）。
- [ ] 已产出真实渲染 PNG/PDF 预览；缺渲染工具时已在 `limitations` 说明。
- [ ] 每个 entity/relation/annotation/图例均含 `source_ref`，IR 含逐字 `quote`。
- [ ] 所有 relation 的 from/to 指向存在的实体/步骤/阶段，无断头箭头。
- [ ] 箭头方向与材料流向一致，回流用 dashed，无反。
- [ ] 图例颜色/线型与图形一致，无“图例与图形不一致”。
- [ ] 数值标注与来源一致，无凭空补齐的部件、管路或数值。
- [ ] 步骤编号与正文顺序逐条对照。
- [ ] 所有实体在画布内，无溢出裁剪。
- [ ] 已运行 `scripts/figure.py validate`，critical 项全部通过；未通过项已在 `uncertainty`/`limitations` 说明。
- [ ] 已运行 `scripts/selftest.py` 且全部通过。

## 3. 常见失败样例（须规避）

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

## 4. 失败处置

- 同一策略失败两次即换策略；错误信息写入 `change_log` 与 `limitations`。
- 无法判断的部件/数值/来源标注“无法判断”，禁止编造。
- 空输入/坏 JSON → 输出最小合法结果（status=insufficient_evidence），退出码 0。

## 5. 最小调用示例（示意输出，非正式案例）

```bash
cd /skills/weekly-skill
python scripts/main.py init --case /input/case.json --out /app/output/result.json
python scripts/figure.py build --ir /app/work/ir.json --out /app/work/figure.svg
python scripts/figure.py validate --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/work/checks.json
python scripts/main.py merge --work /app/work --out /app/output/result.json
python scripts/main.py finalize --status complete --out /app/output/result.json
```

一键确定性跑通（演示/CI 用）：

```bash
python scripts/main.py run --input /input --output /app/output --work /app/work
python scripts/selftest.py
```
