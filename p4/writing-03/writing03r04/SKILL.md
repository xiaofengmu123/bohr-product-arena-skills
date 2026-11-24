---
name: academic-format-adaptation
description: 面向学术格式与模板适配任务的写作 Skill。当需要依据给定科研稿件与目标模板完成格式适配、标题层级与编号、图表与参考文献格式、转换完整性核对或导出校验，且必须为每个检查项保留“模板规格—来源定位—应用结果—核验状态”可回溯记录、显式记录 limitations/uncertainty 且缺数据不编造、最终产出 /app/output/result.json 时使用。
version: 1.1.0
---

# 学术格式与模板适配 Skill

## 1. 角色与何时使用

- 角色：学术格式与模板适配的执行器与质检器。核心目标不是“把格式改得看起来像”，而是让模板规格可逐项核对、内容单元不丢失、导出结果可打开可复核，并让每一项结论都能被指认“依据在哪、核验到什么程度”。
- 何时触发：当 `/input/case.json` 要求依据给定稿件与模板完成格式适配、标题层级、图表/参考文献格式、转换完整性或导出校验，并要求保留来源、修改记录与不确定性时。
- 何时不用：纯创意写作、无模板/稿件输入的闲聊式扩写、超出给定材料的格式臆测。

## 2. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、任务说明、稿件与模板材料清单、`output_contract`、评分所需证据范围）与 `/input/task.md`（题面与运行时约定）。
- 科研/模板材料：由 `case.json` 指明的稿件、模板、院系规定等文件。PDF 用平台 `pdf-text-tool` 抽取全文（必要时 `ocr-tool`、`pdf-render-tool`）；DOCX/PPTX/XLSX 用 `office-renderer` 或平台工具读取；实际文件内容与实测字体才是事实依据，不按文件名或领域标签脑补。
- 工作目录：`/app/work`（中间产物、抽取文本、预览）；最终实际结果与可编辑产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。

## 3. 输出契约

必产 `/app/output/result.json`，字段 = `case_id` + `case.json` 的 `output_contract` 全部字段 + 以下保留字段：

- `status`：`complete | partial | failed | insufficient_evidence | input_error`，诚实标注。
- `deliverable`：目标产物路径与说明；可编辑产物（md/tex/docx）+ 渲染预览（pdf/png）路径回填此处。
- `fact_source_map`：数字/术语/引用/图表/关键论断的来源或修改记录，每项含 `item_id / item_type / check / spec_source / applied_value / verification / status / evidence`。
- `version_boundary`：修改差异、人工确认项、材料缺口、异常。
- `sources`：来源清单（`id/type/title/path/doi/accessed/locator/verification`）。
- `uncertainty`：`claims_requiring_confirmation / conflicting_specs / missing_assets / material_gaps / not_verifiable / degradations / limitations`。
- `pending_human`：需人工确认项清单。
- `change_log`：`[{ts, action, detail}]`，每次落盘追加一条。

若 `case.json` 无 `output_contract`，用三段式兜底：`deliverable`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。

## 4. 执行流程（900 秒 / 64 请求）

硬约束：**前 3 分钟（180 秒）内必须先落盘一版 `result.json` 骨架，之后只做增量改进，不得整体重建。**

- 阶段 0 盘点（0–60s，≤2 请求）：读 `case.json` + `task.md`，解析 `case_id`、`output_contract`、材料清单，列出 `/input` 文件。输入：两个原始文件；输出：任务理解 + 材料清单。
- 阶段 1 骨架落盘（≤180s 前完成，≤1 请求）：运行 `python scripts/main.py --case /input/case.json --task /input/task.md --out /app/output --work /app/work --material-root /input --material-root /app/work`，确认 `case_id` 正确、契约字段齐全。输入：`/input/case.json`；输出：`/app/output/result.json`（`status=partial`）。
- 阶段 2 模板识别与检查项清单（60–300s，≤20 请求）：从模板/规定抽取可逐项核对的规格（栏数、字号、行距、页边距、标题层级、图表题注、参考文献样式、导出格式），生成 `format_specs`；模板版本差异与相互冲突的规定并列记录为 `conflicting_specs`，不擅自取舍。输入：模板/规定全文；输出：检查项清单草案。
- 阶段 3 格式适配与内容单元核对（180–540s，≤20 请求）：按检查项执行适配；逐条记录每个内容单元（正文、图表、公式、脚注、批注、修订、域代码、目录）的保留/修改状态，改写或图片化必须说明；数字/术语/引用逐条配 `source_ref`，校验“原文是否真的支持”。输入：检查项清单 + 稿件全文；输出：`fact_source_map` + `version_boundary`。
- 阶段 4 导出校验与可编辑产物（360–780s，≤15 请求）：选择导出格式、实测页面尺寸/页边距/字体嵌入、刷新目录页码；保存 md/tex/docx 产物 + pdf/png 预览；回填 `artifact_path`。输入：`fact_source_map`；输出：`deliverable` + 产物文件。
- 阶段 5 自检与收尾（720–900s，≤6 请求）：运行 `python scripts/verify_contract.py --result /app/output/result.json --case /input/case.json --report /app/output/verification_report.json --material-root /input --material-root /app/work`；修复硬性错误（缺字段、摘录无法定位、占位符）；写 `status/uncertainty/change_log`；预留 ≤2 请求重试。输入：`result.json` + 材料；输出：最终 `result.json` + `verification_report.json`。

每一步都把增量写回 `result.json` 并追加 `change_log`；任一步超时或请求耗尽时保持 `status=partial`，如实写 `limitations/uncertainty`，绝不编造。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] `case_id` 与 `/input/case.json` 一致（任务完成度/完整性）
- [ ] `result.json` 含 `output_contract` 全部字段 + `status/sources/uncertainty/pending_human/change_log`（结果完整性）
- [ ] 模板规格逐项有 `spec_source`（locator + 逐字 quote），实测值标 `verification=measured`，推断值标 `asserted`，未核验标 `unverified`（认得准/结果可核验性）
- [ ] 标题层级、编号、样式绑定、图表题注与交叉引用、参考文献条目、页眉页脚逐项符合目标模板，而非仅外观相似（排得对）
- [ ] 内容单元逐项记录保留/修改/删除状态，改写或图片化已说明（转得全）
- [ ] 数字/术语/引用/图表逐条有来源，未新增材料外事实（结果正确性）
- [ ] 引用真实存在、元数据准确，排除二手转引/撤稿/版本差异（结果正确性）
- [ ] 导出文件可打开、字体嵌入实测、页码目录可用，可编辑源文件保留（交得出）
- [ ] 冲突规格、缺失字体、材料缺口、未核验项写入 `uncertainty/version_boundary`，不擅自补全（降级与不确定性）
- [ ] 可编辑产物 + pdf/png 预览已产出并回填路径（任务完成度）
- [ ] 无 TODO/占位符/编造标记，`status` 诚实，缺数据标 `partial/insufficient_evidence`（异常处理/可靠体验）
- [ ] 复现路径完整：检查项编号 → 规格摘录 → 定位 → 核验方法 → 理由可独立回查（复得现）

## 6. 降级策略（本版重点：limitations/uncertainty 显式化，缺数据不编造）

- 缺模板 / 只有摘要 / 模板文件不可得：如实记入 `uncertainty.material_gaps`，只写“据现有材料”，不得补写模板规格细节。
- 模板版本差异或相互冲突：全部列入 `uncertainty.conflicting_specs`，正文标注“存在冲突/尚不明确”，不偏选单边；如需取舍，`version_boundary` 记录取舍依据并标 `pending_human`。
- 缺字体（如未提供 Liberation Serif）：记入 `uncertainty.missing_assets` 与 `pending_human`，不得声称字体已嵌入；渲染产物缺失时标 `degradations`，不伪造渲染文件。
- 无法实测（栏数/字号/行距/页边距等无法用工具验证）：对应项标 `verification=unverified` 并进入 `not_verifiable`，只写“待实测”，不写虚假数值。
- 原文与摘要矛盾、证据冲突：全部列入 `uncertainty`，正文用“存在争议/尚不明确”，不偏向单边。
- 无法校验来源真实性：`sources` 标 `verification="unverified"`，`change_log` 记录核验路径。
- 硬性失败（缺 `case_id`/字段/JSON 无效）：`verify_contract.py` 退出码 2，先修复再继续；仍无法修复时 `status=failed` 并写明原因。
- 任何情况禁止编造评测分数、运行结果、文献元数据、字体或科学数据；缺数据写 `not_reported` 或 `pending_human`，不补写。

## 7. 脚本与资源

- `python scripts/main.py [--case /input/case.json] [--task /input/task.md] [--out /app/output] [--work /app/work] [--material-root /input]`：盘点输入并幂等落盘/增量合并 `result.json` 骨架（退出码 0）。
- `python scripts/verify_contract.py [--result /app/output/result.json] [--case /input/case.json] [--report /app/output/verification_report.json] [--material-root /input] [--material-root /app/work]`：契约 + 摘录可核验性自检；退出码 0=通过，1=软警告，2=硬失败。
- `python scripts/selftest.py [--skill-root .]`：在临时目录构造正常输入/空输入/坏 JSON 三种用例，验证主流程合法性、降级与幂等。
- `references/format-check-schema.md`：检查项与内容单元的记录结构、核验口径。
- `references/degradation-rubric.md`：降级与不确定性表达口径、失败类型与示例。
- `references/minimal-example.md`：最小调用示例（输入、调用方式、示意输出，均标明为示例，不冒充正式案例结果）。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用。
