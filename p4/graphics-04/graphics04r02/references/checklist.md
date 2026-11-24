# 自检清单、常见失败样例与降级登记表

## 1. 交付前逐条勾选

- [ ] 已产出 `/app/output/result.json`，含 `case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`change_log` 与六类契约字段。
- [ ] 已产出可编辑分层 SVG（`layer-relations` / `layer-entities` / `layer-annotations` / `layer-legend`）。
- [ ] 已产出真实渲染 PNG/PDF 预览；缺渲染工具时已在 `limitations` 说明。
- [ ] 每个 entity/relation/annotation/图例均含 `source_ref`，可追溯到输入材料原文，且带逐字 `quote`。
- [ ] 所有 relation 的 `from`/`to` 指向存在的实体，无断头箭头。
- [ ] 箭头方向与材料流向一致，回流/反馈用 dashed，无反向。
- [ ] 图例颜色/线型与图形一致，无"图例与图形不一致"。
- [ ] 数值标注与来源一致，无凭空补齐的部件、管路或数值。
- [ ] 步骤编号与正文顺序逐条对照；并行/循环/终止条件明确。
- [ ] 所有实体在画布内，无溢出。
- [ ] 已运行 `python scripts/selftest.py --ir ir.json --svg figure.svg --out checks.json`，critical 项全部通过；未通过项已在 `uncertainty`/`limitations` 说明。
- [ ] p2 阶段骨架在 180 秒内落盘，`result.json` 含 `budget` 与 `phase`。

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

## 3. 降级登记表（缺数据/缺软件如实标注，禁止编造）

| 场景 | 处置 | result.json 记录 |
|-|-|-|
| 材料缺失/无法解析 | 不补造部件、数值、来源 | `uncertainty` + `limitations` = "无法判断" |
| 渲染工具缺失 | 只交 SVG，不伪造 PNG/PDF | `exports.render_status="skipped"`，`limitations` 说明 |
| 中文字体缺失 | `font-discovery` 查字体，缺则降级英文标签 | `exports.fonts` 标注，不假装嵌入成功 |
| 脚本报错 | 读 stderr 定位；同策略失败两次即换 | `change_log` + `limitations` |
| 坏 JSON/坏编码 | utf-8-sig/utf-8/errors=replace 容错 | `pending_human` / `errors` |
| 坐标/接口冲突 | 遵循材料原文，标不确定 | `uncertainty` |
| 空输入目录 | 退出码 0，写合法结果 | `status="failed"` 或 `insufficient_evidence` |
| 写盘失败 | 降级到最小合法产物 | `limitations` |

## 4. 复跑约定

- 同一输入两次运行 `build_svg.py` 结果一致（确定性布局，无随机种子差异）。
- `selftest.py --smoke` 自举：临时生成示意 IR → build_svg → 校验，证明脚本可跑，产物标注为示意，不冒充正式案例。
- 提交前在干净目录跑 `python scripts/main.py discover --input <dir>`、`init`、`merge`、`finalize` 各一次，确认退出码 0 且 result.json 可解析。
