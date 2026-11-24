---
name: academic-format-template-adaptation
description: 面向学术格式与模板适配的写作 Skill。当需要依据给定科研材料与目标模板完成稿件格式适配、标题层级、图表题注与交叉引用、参考文献排版、格式转换与导出，且必须为每条结论/每次改动保留"逐字摘录—来源定位—证据强度—不确定性"可回溯证据链，并最终产出 /app/output/result.json 时使用。
version: 1.0.0
---

# 学术格式与模板适配 Skill（证据溯源强化版）

## 1. 角色与何时使用

- 角色：证据驱动的"格式适配执行器 + 质检器"。核心不是把字改大、加粗，而是让每一条格式结论、每一次内容改动都能被指认"依据在哪、依据多强、改动前后各是什么"。
- 何时触发：`/input/case.json` 要求依据给定科研材料与目标模板完成学术写作、格式适配、标题层级、图表/参考文献格式、格式转换或导出，并要求保留来源、修改记录与不确定性时。
- 何时不用：纯创意写作、无来源要求的闲聊扩写、超出给定材料与模板范围的自创规格。

## 2. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、任务说明、材料清单、`output_contract`、评分所需证据范围）与 `/input/task.md`（题面与运行时约定）。
- 科研材料与模板：由 `case.json` 指明的论文/模板/规定文档。PDF 用 `pdf-text-tool` 抽全文（必要时 `ocr-tool`、`pdf-render-tool`）；Office 模板用 `office-renderer` 渲染并抽取样式/字段；**实际文件内容才是事实依据**，不按文件名或领域标签脑补。
- 工作目录：`/app/work`（中间产物、抽取文本、预览、`inventory.json`）；最终实际结果与可编辑产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。

## 3. 输出契约

必产 `/app/output/result.json`，顶层 = `case_id` + `case.json` 的 `output_contract` 全部字段 + 以下保留字段：

- `status`：`partial | complete | failed | insufficient_evidence | input_error`，诚实标注。
- `evidence_map`：`template_specs`（认得准）、`applications`（排得对）、`content_units`（转得全）、`export_checks`（交得出）四张子表；每条记录必须含 `id/quote/locator/source_ref/evidence_level`。结构见 `references/format-spec-schema.md`。
- `sources`：来源清单（`id/type/title/path/doi/accessed/locator/verification`）。
- `uncertainty`：`claims_requiring_confirmation`、`conflicting_evidence`、`material_gaps`、`limitations`。
- `pending_human`：需要人工确认的项。
- `change_log`：`[{ts, action, detail}]`，每次落盘追加一条。

若 `case.json` 无 `output_contract`，用三段式兜底：`deliverable`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。每个案例还需产出可编辑产物（md/tex/docx）及其真实渲染预览（pdf/png）到 `/app/output/`，并把路径回填进 `result.json` 的 `deliverable` 与 `version_boundary`。

## 4. 执行流程（900 秒 / 64 请求）

硬约束：**前 3 分钟（180 秒）内必须先运行 `scripts/main.py` 落盘一版 `result.json` 骨架（`status=partial`），之后只做增量改进，不得整体重建。**

- 阶段 0 盘点（0–60s，≤2 请求）：读 `case.json` + `task.md`，解析 `case_id`、`output_contract`、材料清单与目标模板，列出 `/input` 文件。输入：两个原始文件；输出：任务理解 + 材料/模板清单。
- 阶段 1 骨架落盘（≤180s 前完成，≤1 请求）：`python scripts/main.py --case /input/case.json --output /app/output --work /app/work`，确认 `case_id` 正确、契约字段齐全。输出：`result.json`（`status=partial`）+ `/app/work/inventory.json`。
- 阶段 2 模板规格提取（60–300s，≤20 请求）：从模板/规定抽取栏数、字号、行距、页边距、标题层级与编号、图表题注、参考文献样式、页眉页脚、导出格式，逐项写入 `evidence_map.template_specs`（每条带 `quote/locator/evidence_level`）；版本差异与冲突规定全部并列，不隐藏。输入：模板全文；输出：规格检查项清单。
- 阶段 3 内容单元清点与落位（180–540s，≤20 请求）：清点原稿的正文、图、表、公式、脚注、批注、修订、域代码、分节符、目录，写入 `content_units`；把每条规格的"应用—before/after"写入 `applications`；每条论断校验"原文是否真的支持"。输入：材料全文 + 规格清单；输出：`evidence_map`。
- 阶段 4 成稿与导出校验（360–780s，≤15 请求）：按目标模板写/改可编辑产物，保存 md/tex/docx + pdf/png 预览；运行导出校验（可打开、字体嵌入、页码与目录、页边距实测）写入 `export_checks`；回填 `deliverable`/`artifact_path`。输入：`evidence_map`；输出：成品 + 预览。
- 阶段 5 自检与收尾（720–900s，≤6 请求）：`python scripts/selftest.py --result /app/output/result.json --case /input/case.json --report /app/output/verification_report.json --material-root /input --material-root /app/work`；修复硬性错误；写 `status/uncertainty/pending_human/change_log`；预留 ≤2 请求重试。输出：最终 `result.json` + `verification_report.json`。

每一步把增量写回 `result.json` 并追加 `change_log`；任一步超时或请求耗尽时保持 `status=partial`，如实写 `limitations`，绝不编造。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] `case_id` 与 `/input/case.json` 一致（任务完成度）
- [ ] `result.json` 含 `output_contract` 全部字段 + `status/sources/uncertainty/change_log`（结果完整性）
- [ ] `evidence_map` 四张子表齐备，每条记录含 `id/quote/locator/source_ref/evidence_level`（找得到/对得上）
- [ ] 每条 `quote` 经 `selftest.py` 能在材料全文定位，未定位的已标 `limitations`（结果可核验性）
- [ ] 模板规格（字号/行距/页边距/栏数/题注/参考文献样式）逐项有原文依据，不凭经验猜测（结果正确性）
- [ ] 标题层级、编号、图表题注与交叉引用、参考文献条目、页眉页脚均有 `applications` before/after 记录（排得对）
- [ ] 正文、图、表、公式、脚注、批注、修订、域代码、目录均在 `content_units` 清点，未丢失/未改写（转得全）
- [ ] 导出文件可打开、字体嵌入、页码与目录可用、可编辑源文件保留，`export_checks` 逐项记录（交得出）
- [ ] 数字/术语/引用逐条有来源，未新增材料外事实；冲突证据未隐藏（说得准）
- [ ] 未确认项、材料缺口、异常写入 `version_boundary/uncertainty/pending_human`（版本与边界）
- [ ] 可编辑产物 + pdf/png 预览已产出并回填路径（任务完成度）
- [ ] 无 TODO/占位符/编造标记，`status` 诚实，缺数据标 `partial/failed/insufficient_evidence`（异常处理）
- [ ] 复现路径完整：记录编号 → 摘录 → 定位 → 来源 → 强度理由可独立回查（复现性）

## 6. 降级策略

- 缺材料 / 只有摘要 / 全文不可得：如实记入 `uncertainty.limitations`，只写"摘要表明"，不补正文细节；`status=partial` 或 `insufficient_evidence`。
- 缺软件（`pdf-text-tool/ocr/latex/office-renderer` 不可用）：标准库脚本仍可读纯文本；标注"预览未生成/依赖不可用"，不伪造渲染文件。
- 模板缺失/版本不明、字体缺失、样式冲突、转换失败、内容无法保真：逐项写 `degradations`/`uncertainty.material_gaps`，需人工判断的写 `pending_human`。
- 原文与规定矛盾、证据冲突：全部列入 `conflicting_evidence`，正文用"存在冲突/尚不明确"，不偏单边。
- 无法校验来源真实性：`sources` 标 `verification="unverified"`，`change_log` 记录核验路径。
- 硬性失败（缺 `case_id`/字段/JSON 无效）：`selftest.py` 退出码 2，先修复再继续；修复不了保持 `partial/failed` 并如实写 `limitations`。
- 任何情况禁止编造评测分数、运行结果、文献元数据或科学数据。

## 脚本与资源

- `python scripts/main.py [--case /input/case.json --output /app/output --work /app/work]`：骨架落盘（幂等，增量合并）+ 输入清单。
- `python scripts/selftest.py [--result /app/output/result.json --case /input/case.json --report /app/output/verification_report.json --material-root /input --material-root /app/work]`：契约 + 逐字摘录可定位性 + 产物自检；退出码 0/1/2。
- `references/format-spec-schema.md`：evidence_map 四张子表的结构规范与复现路径。
- `references/evidence-rubric.md`：证据强度分级、表述口径与降级规则。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用（安装后为 `/skills/weekly-skill/scripts/main.py`）。
