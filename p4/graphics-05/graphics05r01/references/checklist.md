# 交付自检清单（对应评分维度）

定稿前逐条勾选。`scripts/selftest.py` 自动检查其中可程序化项（schema、SVG 可解析、层级、文字可编辑、
evidence_map 每条带 locator+quote、空输入降级）。

## A. 结果完整性与可核验性（实际效果·结果完整性 / 可核验性）

- [ ] `result.json` 含 `case_id`，且原样回填题目 `output_contract` 全部字段（缺值 `null`，未知键不删）。
- [ ] 含 `status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`evidence_map`。
- [ ] `sources[]` 每条含 `source_id/path/type/title`，且确实存在于 `/input` 或材料中。

## B. 实体关系与数据映射正确（实际效果·结果正确性）

- [ ] 每个框/箭头/柱/图例色块有唯一 id，映射到 `entities/relations/data_mapping/legend` 条目。
- [ ] 箭头方向与原文因果方向一致；有向/无向连接与 `relations[].kind` 一致。
- [ ] 每个数值带单位与条件标签；色阶/长度/面积编码与原始数据一一对应。

## C. 四要素与辅助信息覆盖（实际效果·任务完成度）

- [ ] `graphical_abstract` 四要素 `question/method/results/conclusion` 全部非空。
- [ ] 图例、单位、比例尺、图注、面板标题齐全，阅读顺序「问题→方法→结果→结论」明确。

## D. 可编辑源文件与导出（实际效果 + 可靠体验）

- [ ] SVG 为分层 `layer-*` 命名组，文字保持 `<text>`，无 text-as-path。
- [ ] 元素带 `data-source`/`data-locator` 属性。
- [ ] PNG/PDF 为真实文件（`%PDF-` 开头 / 可解析位图）；无渲染器则 `preview_not_rendered`，不写假图。

## E. 证据溯源（本版强化）

- [ ] `evidence_map.claims[]` 非空，每条 `locator`+逐字 `quote`+`source_ref` 齐全。
- [ ] 每个实体/关系/数据/图例项同样带 `locator`+`quote`；`maps_to` 双向可核验。
- [ ] 冲突记入 `conflicts[]`，缺失/歧义记入 `unresolved[]`/`pending_human[]`，不编造。

## F. 版式与视觉层级（可靠体验·异常处理）

- [ ] 无画布溢出、字号 <8px、文字重叠；低对比与色觉差异已处理，灰度可区分。

## G. 稳定性与可复核（可靠体验·运行稳定性）

- [ ] 同一输入两次运行得到同一 SVG 结构（确定性布局，无随机数）。
- [ ] `change_log` 每次重写追加一条（rev 递增）。

## H. 异常与降级

- [ ] 缺数据/数值填 `null` 并记 `uncertainty`；不估算。
- [ ] 缺渲染器/字体/软件如实记 `limitations`；SVG 仍为主产物。
- [ ] 空输入/坏 JSON 时脚本退出码 0（或失败时仍留合法 result.json），错误入 `errors/degradations`。

## I. 生态贡献与复用

- [ ] 脚本只用标准库，相对路径 `scripts/*.py` 可执行，无个人隐藏文件。
- [ ] `--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。
- [ ] `python -B scripts/selftest.py` 退出码 0。

## 评分维度 → 检查项映射

| 评分维度 | 对应检查节 | 主要考察 |
|-|-|-|
| 实际效果（40） | A、B、C、D、E | 四要素成图、实体关系/数据映射正确、完整性、可核验 |
| 使用增益（25） | D、G、H | 结构一致、可编辑源文件减少重绘、对照可验证 |
| 创新增益（15） | E、D | 证据双向溯源、确定性布局、分层可编辑导出 |
| 可靠体验（10） | D、F、G、H | 触发易用、重复运行稳定、异常识别处置 |
| 生态贡献（10） | I、A | 文档/模板/依赖/导出参数齐全、脚本可复用 |

