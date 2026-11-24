---
name: academic-format-adapter
description: 面向学术格式与模板适配任务的写作 Skill。当需要依据给定科研材料把稿件按目标模板完成改稿、标题层级、图表题注、参考文献样式、转换完整性与导出，并保留“模板规格—内容单元—样式映射—保真度自检”四类结构化中间产物、最终产出 /app/output/result.json 时使用。
version: 1.0.0
---

# 学术格式与模板适配 Skill

## 1. 角色与何时使用

- 角色：学术格式与模板适配执行器与保真度质检器。目标不是“看起来像模板”，而是让每一项格式规格都可核对、每个内容单元都锚定来源、每次转换都有完整性比对。
- 何时触发：当 `/input/case.json` 要求依据给定科研材料完成改稿、标题层级、图表与参考文献格式、格式转换、导出或结构化写作，并需保留事实、数字与引用来源时。
- 何时不用：纯创意写作、无材料与无格式要求的闲聊扩写、超出给定材料的领域外推测。

## 2. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、任务说明、材料清单、`output_contract`、评分证据范围）与 `/input/task.md`（题面与运行时约定）。
- 科研材料：由 `case.json` 指明的论文/模板/文档。PDF 用平台 `pdf-text-tool` 抽全文（必要时 `ocr-tool`、`office-renderer`）；`latex-compiler`、`font-discovery`、`office-renderer` 用于编译与导出。实际文件内容才是事实依据，不按文件名或领域标签脑补。
- 工作目录：`/app/work`（中间产物、抽取文本、中间表示）；最终可编辑产物与渲染预览写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。

## 3. 输出契约

必产 `/app/output/result.json`，字段 = `case_id` + `case.json` 的 `output_contract` 全部字段 + 以下保留字段：

- `status`：`complete | partial | failed`，诚实标注。
- `sources`：来源清单（`id/type/title/path/doi/accessed/locator`），每条来源至少能指回文件与定位。
- `uncertainty`：`claims_requiring_confirmation`、`conflicting_evidence`、`material_gaps`、`limitations`。
- `pending_human`：需人工确认项清单（`item/reason/suggested_action`）。
- `change_log`：`[{ts, action, detail}]`，每次落盘追加一条。
- `intermediates`：四类中间表示产物路径回填（见下节）。

若 `case.json` 无 `output_contract`，用三段式兜底：`deliverable`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。每个案例还需产出可编辑产物（md/tex/docx）及其渲染预览（pdf/png）到 `/app/output/`，并把路径回填进 `result.json` 的 `deliverable`。

## 4. 中间表示与可复核设计（本 Skill 核心取舍）

不把“格式判断→改稿→核对”压成一段不可回查的文本，而是落成四个可单独复核的 JSON，全部存 `/app/work/intermediates/`：

1. `format_spec.json` —— 模板规格模型：栏数、页边距、字号、行距、标题层级、图注/表注位置与字号、参考文献样式、页眉页脚、导出格式等**可逐项核对的规格**，每条附 `locator`（来自模板文件/补充规定的定位），冲突规格并列。
2. `content_units.json` —— 内容单元清单：把原稿切分为 heading/paragraph/figure/table/formula/footnote/comment/revision/field_code/toc/reference 单元，每单元给 `id/locator/excerpt/target_style/status`。`status` 只允许 `pending|kept|mapped|missing|dropped`。
3. `plan.json` —— 适配方案：目标模板、体裁/受众、样式映射（源样式→目标样式）、内容锚定、逐项核对点。
4. `fidelity_report.json` —— 保真度自检：转换前后内容单元计数、丢失单元、被误改单元、异常与结论（`pending|pass|fail`）。

四者由 `scripts/build_intermediates.py` 生成骨架并回填路径；`scripts/selftest.py --verify` 校验它们的存在性与结构合法性。核对顺序固定为：认得准（format_spec）→ 排得对（plan）→ 转得全（content_units）→ 交得出（fidelity_report）。

## 5. 执行流程（900 秒 / 64 请求）

硬约束：**前 3 分钟（180 秒）内先运行 `python scripts/scaffold_result.py` 落盘一版 `status=partial` 的 `result.json`，之后只做增量改进，不得整体重建。**

- 阶段 0 盘点（0–60s，≤2 请求）：读 `case.json` + `task.md`，解析 `case_id`、`output_contract`、材料清单，列出 `/input` 文件。输入：两个原始文件；输出：任务理解 + 材料清单。
- 阶段 1 骨架落盘（≤180s 前完成，≤1 请求）：运行 `python scripts/scaffold_result.py`，确认 `case_id` 正确、契约字段齐全。输出：`result.json`（`status=partial`）。
- 阶段 2 模板规格提取（60–240s，≤12 请求）：从模板/规定抽取格式规格填 `format_spec.json`，逐项带定位；无模板时从题面约束提取并标 `uncertain`。输入：模板/规定全文；输出：`format_spec.json`。
- 阶段 3 内容单元与适配方案（180–480s，≤20 请求）：切分原稿为内容单元填 `content_units.json`；建立样式映射与核对点填 `plan.json`；数字/术语/引用每条配来源定位与逐字摘录。输入：材料全文；输出：`content_units.json` + `plan.json` + `fact_source_map`。
- 阶段 4 成稿与导出（420–780s，≤20 请求）：按目标模板改稿并导出可编辑产物（md/tex/docx）+ pdf/png 预览；运行 `fidelity_report.json` 完整性比对；回填 `deliverable` 与 `intermediates` 路径。输入：三个中间产物；输出：成品文件 + 保真度报告。
- 阶段 5 自检与收尾（720–900s，≤6 请求）：运行 `python scripts/selftest.py --verify`；修复硬性错误（缺字段、定位无法回溯、占位符）；写 `status/uncertainty/pending_human/change_log`；预留 ≤2 请求重试。

每一步都把增量写回 `result.json` 并追加 `change_log`；任一步超时或请求耗尽时保持 `status=partial`，如实写 `limitations`，绝不编造。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] `case_id` 与 `/input/case.json` 一致（任务完成度）
- [ ] `result.json` 含 `output_contract` 全部字段 + `status/sources/uncertainty/pending_human/change_log/intermediates`（结果完整性）
- [ ] 四个中间产物存在且结构合法（结果可核验性）
- [ ] 每条格式规格有 `locator`；每条数字/术语/引用有来源定位 + 逐字 quote（找得到/对得上）
- [ ] 每个内容单元有 `id/locator/excerpt/target_style/status`，无 `pending` 残留（转得全）
- [ ] `fidelity_report` 含转换前后计数、丢失/误改单元，结论诚实（转得全/交得出）
- [ ] 标题层级、编号、图注表注、参考文献、页眉页脚逐项符合目标模板，不只改外观（排得对）
- [ ] 冲突规格并列、未确认项写入 `uncertainty/pending_human`，不隐藏（说得准）
- [ ] 可编辑产物 + pdf/png 预览已产出并回填路径（交得出）
- [ ] 无 TODO/占位符/编造标记，`status` 诚实，缺数据标 `partial/failed`（异常处理）
- [ ] 复现路径完整：规格→映射→单元→自检四层可独立回查（复得现）

## 7. 降级策略

- 缺材料/只有摘要/全文不可得：如实记入 `uncertainty.limitations`，只写“摘要表明”，不补写正文细节。
- 缺模板或模板版本不明：`format_spec` 标 `status=unknown`，规格全部进 `uncertain`，正文只按题面明确约束执行。
- 缺软件（`pdf-text-tool/ocr/latex/office-renderer/font-discovery` 不可用）：标准库脚本仍可读纯文本；标注“预览未生成/依赖不可用”，不伪造渲染文件。
- 字体缺失：用 `font-discovery` 找替代字体并写入 `uncertainty.limitations`，不静默替换后谎称嵌入。
- 原文与摘要矛盾、证据冲突：全部列入 `conflicting_evidence`，正文用“存在争议/尚不明确”，不偏向单边。
- 无法校验来源真实性：`sources` 标 `verification="unverified"`，`change_log` 记录核验路径。
- 硬性失败（缺 `case_id`/字段/JSON 无效）：`selftest.py --verify` 退出码 2，先修复再继续。
- 任何情况禁止编造评测分数、运行结果、文献元数据、模板规格或科学数据。

## 脚本与资源

- `python scripts/scaffold_result.py [--case /input/case.json --out /app/output --work /app/work]`：骨架落盘（幂等，增量合并，不覆盖已填内容）。
- `python scripts/build_intermediates.py [--work /app/work --result /app/output/result.json] [--finalize]`：生成四个中间表示骨架，`--finalize` 时把中间产物路径与保真度结论合并回 `result.json`。
- `python scripts/selftest.py [--selftest|--verify] [--result /app/output/result.json --case /input/case.json --report /app/output/verification_report.json --material-root /input --material-root /app/work]`：包自检 + 契约/定位可核验性自检。
- `references/format-spec-schema.md`：模板规格字段口径与常见失败类型。
- `references/integrity-checklist.md`：转换完整性与导出可用性检查清单。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用。
