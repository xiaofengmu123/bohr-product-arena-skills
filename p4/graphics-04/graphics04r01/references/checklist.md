# 自检清单与常见失败样例（scix-figure-evidence）

## 1. 交付前逐条勾选（对应评分维度）

实际效果：
- [ ] 已产出可编辑分层 SVG（`layer-entities` / `layer-relations` / `layer-steps` / `layer-legend`），不是纯文字或单张截图。
- [ ] 每个实体/关系/图例/标注均带 `source_ref` 与 `quote`，可追溯到输入材料原文。
- [ ] 部件名称/数量/接口与材料一致；连接方向正确；箭头无反向；回流用 dashed。
- [ ] 数值标注与来源一致，无凭空补齐的部件、管路或数值。
- [ ] 六类契约字段（entities / relations / data_encoding / legend_annotations / editable_sources / exports）齐全。
- [ ] 已交付真实渲染 PNG/PDF 预览；缺渲染工具时在 `limitations` 说明，`exports.render_status="skipped"`。

可核验性：
- [ ] result.json 含 `case_id`、`status`、`sources`、`evidence_map`、`uncertainty`、`pending_human`、`change_log`。
- [ ] `evidence_map.coverage.quote_coverage` 已写入，未溯源项在 `unquoted` 显式列出。
- [ ] SVG 嵌入 `scix-evidence-map`，评审可独立复核。

可靠体验：
- [ ] `python scripts/selftest.py` 通过（空输入、坏 JSON、正常输入三种情形）。
- [ ] 同一输入重复运行结果一致（无随机数）。
- [ ] 脚本仅标准库、相对路径调用；退出码 0 表示成功或诚实降级。

生态贡献：
- [ ] `references/ir_schema.md` 写清 IR 字段与证据溯源要求。
- [ ] `references/checklist.md` 写清失败样例与处置。

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
11. `quote` 被改写/意译而非逐字原文。
12. 无法溯源的元素被当作已确认（未写 `method="derived"`）。

## 3. 失败处置

- 同一策略失败两次即换策略；错误信息写入 `change_log` 与 `limitations`。
- 无法判断的部件/数值/来源标注"无法判断"，`confidence="low"`，禁止编造。
- 渲染工具缺失：只交付 SVG，`render_status="skipped"`，不伪造 PNG/PDF。
- 中文缺字：用 `font-discovery` 查字体；缺中文字体时在 `exports.fonts` 标注并降级英文标签。
