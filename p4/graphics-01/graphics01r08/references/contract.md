# 输出契约与评分维度对齐（contract.md）

本文件定义 `result.json` 的字段语义、输入发现规则，以及评分维度到流程步骤与自检项的映射。运行时任何脚本不得依赖本文件之外的未说明约定。

## 1. 输入发现规则

1. 不假设固定文件名：`case.json`、`task.md`、`materials/`、`notes.md` 均需能识别；用 `python scripts/main.py discover --input <dir>` 输出全部文件清单与跳过项。
2. 文本按 `utf-8-sig` 读取，容错 BOM、坏编码、坏 JSON；读取失败记录到 `errors`/`degradations`，继续处理其余材料。
3. 单文件超过 2MB 跳过并记录到 `degradations`。
4. `case.json` 的 `output_contract` 键必须**原样回填**到 `result.json`：缺值给 `null`/`[]`，不改名、不删未知键。

## 2. result.json 字段

| 字段 | 类型 | 语义 |
|-|-|-|
| case_id | string | case.json 的 case_id/id，缺失为 unknown |
| status | string | complete / partial / insufficient_evidence / failed |
| editable_source_files | list | 可编辑源文件：path、kind、layers、editable_text、note |
| export_files | list | 导出件：path、format、width_px、height_px、dpi、color_space、font_check、note |
| provenance | list | 逐元素溯源：element、source_ref、locator、quote（逐字）、mapping、version、confirmed |
| sources | list | 材料引用：ref、path、locator、used_for |
| uncertainty | list | item、reason、impact、mitigation |
| pending_human | list | 需人工确认项：item、why |
| errors | list | 运行错误记录 |
| degradations | list | 降级记录（跳过超大文件、OCR、字体替换等） |
| change_log | list | time、action、detail；记录修改量与返工轮次 |
| runtime | object | started_at、finished_at、seconds、model_requests_used |

状态词汇：

- `complete`：全部交付且 validate 通过；
- `partial`：部分交付，limits 写入 uncertainty；
- `insufficient_evidence`：材料不足以完成，列出缺口，不编造；
- `failed`：运行失败并说明原因。

## 3. 溯源规范（provenance）

每条关键图元、关系、数值、图例项都要有一条 provenance，且必须同时给：

- `locator`：材料定位（页码/节/行列，如 `materials/paper.md#p3` 或 `table1 row2`）；
- `quote`：来源材料中的**逐字片段**（原文摘录，不是转述）；
- `mapping`：该元素在图面的数据/图例映射（如「颜色 #1f77b4 ↔ 分组 A」）。

冲突信息不隐藏：同一指标两个来源不一致时两者都记，并标 `source_conflict`。不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 4. 产物规则

- 可编辑源文件：至少一个分层 SVG（`<g>` 图层、`<text>` 可编辑文字、字体可替换）；图表另附数据文件（CSV/JSON）。Office/PPT 图稿必须同时给真实渲染的 PDF/PNG 预览。
- 导出件：至少一组可打开的 PNG 或 PDF。`validate` 检查：SVG 可解析且含 `<text>`/`<g>`；PDF 以 `%PDF-` 开头；PNG 魔数与 IHDR 尺寸与声明的 width_px/height_px 一致。
- 空数组必须说明原因，不得静默省略。

## 5. 评分维度 → 流程 → 自检项对齐

| 维度(分值) | 关键流程步骤 | 可勾选自检项（见 SKILL.md §5） | 证据载体 |
|-|-|-|-|
| 实际效果(40) | 抽取→布局→成图→导出 | 实体/关系/方向/层级一致、无编造元素、图例一致、provenance 可复核 | result.json provenance/sources、figure.svg |
| 使用增益(25) | 数据同源重绘、可编辑 SVG | 改数据重绘、改标签不重画、change_log 记录返工 | spec/数据文件、SVG、change_log |
| 创新增益(15) | 实体关系抽取、图例驱动、validate | 抽取方法可说明、legend 与 entities 同源、validate 真实运行 | references/figure-grammar.md、脚本、validate 报告 |
| 可靠体验(10) | 骨架先行、异常降级 | 3 分钟内落 partial 骨架、空/坏输入不崩溃、缺软件诚实标注 | main.py scaffold/validate、errors/degradations |
| 生态贡献(10) | 文档、模板、预设复用 | 文档齐全、模板可复用、沉淀经验不预写答案 | SKILL.md、references/、脚本 |
