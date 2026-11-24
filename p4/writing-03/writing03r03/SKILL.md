---
name: academic-format-adaptation
description: 面向“学术格式与模板适配”的写作 Skill。当需要依据给定科研稿件与目标模板完成模板识别、标题层级与编号、图表题注与交叉引用、参考文献格式、转换完整性与导出校验，并为每条格式规格与内容修改保留“来源定位—逐字摘录—证据强度—不确定性”可回溯记录、最终产出 /app/output/result.json 时使用。强调同输入两次运行产物一致（确定性）与可勾选自检。
version: 1.0.0
---

# 学术格式与模板适配 Skill

## 1. 角色与何时使用

- 角色：证据驱动的学术格式适配执行器与质检器。核心目标不是“外观相似”，而是让每一项格式规格与内容修改都能被指认“依据在哪、改了什么、依据有多强”。
- 何时触发：当 `/input/case.json` 要求依据给定稿件与目标模板完成格式适配、改写、校对、引用、双语或结构化写作，并要求保留来源、修改记录与不确定性时。
- 何时不用：纯创意写作、无来源要求的闲聊式扩写、无模板/无稿件可依的领域外推测。

## 2. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、任务说明、材料清单、`output_contract`、评分所需证据范围）与 `/input/task.md`（题面与运行时约定）。
- 科研材料：`case.json` 指明的稿件、目标模板、格式规定与论文/文档。PDF 用平台 `pdf-text-tool` 抽取全文（必要时 `ocr-tool`、`pdf-render-tool`、`office-renderer`）；实际文件内容才是事实依据，不按文件名或领域标签脑补。
- 工作目录：`/app/work`（抽取文本、中间产物、预览）；最终实际结果与可编辑产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。

## 3. 输出契约

必产 `/app/output/result.json`，字段 = `case_id` + `case.json` 的 `output_contract` 全部字段（缺值给 `null`/`[]`，不改名、不删未知键）+ 以下保留字段：

- `status`：`partial | complete | failed | insufficient_evidence | input_error`，诚实标注。
- `sources`：来源清单（`id/type/title/path/doi/locator/verification`）。
- `uncertainty`：`claims_requiring_confirmation`、`conflicting_evidence`、`material_gaps`、`limitations`。
- `pending_human`：需人工确认项。
- `change_log`：`[{step, action, detail}]`，固定 `step` 幂等去重（0=scaffold、10=格式规格提取、20=内容单元清点、30=样式映射与成稿、40=导出、50=seal）。

若 `case.json` 无 `output_contract`，用三段式兜底：`deliverable`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。每个案例还需产出可编辑产物（md/tex/docx）及其渲染预览（pdf/png）到 `/app/output/`，并把路径回填进 `result.json` 的 `deliverable.artifact_paths`。

## 4. 执行流程（900 秒 / 64 请求）

硬约束：**前 3 分钟（180 秒）内必须先落盘一版 `result.json` 骨架，之后只做增量改进，不得整体重建。**

- 阶段 0 盘点（0–60s，≤2 请求）：读 `case.json` + `task.md`，解析 `case_id`、`output_contract`、材料清单、目标模板与格式约束，列出 `/input` 文件。
- 阶段 1 骨架落盘（≤180s 前完成，≤1 请求）：运行 `python -B scripts/main.py scaffold --case /input/case.json --out /app/output --work /app/work`，确认 `case_id` 正确、契约字段齐全、`status=partial`。
- 阶段 2 格式规格提取“认得准”（60–300s，≤20 请求）：从模板/格式要求逐项抽取栏数、字号、行距、页边距、标题层级、图题表题、参考文献样式、导出格式；每条配 `locator` + 逐字 `quote`；冲突规定并列，写入 `fact_source_map` 与 `conflicting_evidence`。
- 阶段 3 内容单元清点“转得全”（60–300s，≤20 请求）：清点正文、图表、公式、脚注、批注、修订、域代码、分节符、嵌入对象、目录；逐项记录“保留/未改动”，转换后比对无删除/无合并/无改写/无图片化。
- 阶段 4 样式映射与成稿“排得对”（180–540s，≤15 请求）：标题层级、编号、样式绑定、目录书签；图表题注与交叉引用；参考文献字段顺序、作者截断、标点、中英文混排；产出可编辑稿到 `/app/output/`。
- 阶段 5 导出与交验“交得出”（360–780s，≤15 请求）：导出、字体嵌入（Liberation Serif）、页边距实测、目录页码刷新；产出 pdf/png 预览，回填 `artifact_paths`。
- 阶段 6 自检与封印（720–900s，≤6 请求）：运行 `python -B scripts/main.py verify`、`seal`、`digest`，写 `status/uncertainty/change_log`，预留 ≤2 请求重试。

每一步把增量写回 `result.json` 并按固定 `step` 追加 `change_log`；任一步超时或请求耗尽时保持 `status=partial`，如实写 `limitations`，绝不编造。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] `case_id` 与 `/input/case.json` 一致（任务完成度）
- [ ] `result.json` 含 `output_contract` 全部字段 + `status/sources/uncertainty/pending_human/change_log`（结果完整性）
- [ ] 每条实质论断/修改都有 `claim_id/claim_text/quote/locator/source_ref/evidence_level`（可核验性）
- [ ] 每条 `quote` 经 `main.py verify` 校验能在材料全文定位，未定位的已标 `limitations`（可核验性）
- [ ] 引用真实存在、元数据准确，排除二手转引/撤稿/版本差异（结果正确性）
- [ ] 证据强度与表述相称：相关≠因果、单篇小样本≠领域共识、冲突证据未隐藏（说得准）
- [ ] 数字/术语/图表/引用逐条有来源，未新增材料外事实（事实与来源映射）
- [ ] 模板规格逐项可核对（栏数/字号/行距/页边距/层级/题注/参考文献/导出格式），非凭经验猜测（认得准）
- [ ] 标题层级/编号/题注/交叉引用/参考文献逐项符合模板（排得对）
- [ ] 正文/图表/公式/脚注/批注/修订/域代码/目录保持完整、无删除改写（转得全）
- [ ] 导出文件可打开、字体嵌入、页码目录可用、可编辑源文件保留（交得出）
- [ ] 未确认项、材料缺口、异常写入 `version_boundary/uncertainty/pending_human`（版本与边界）
- [ ] 可编辑产物 + pdf/png 预览已产出并回填路径（任务完成度）
- [ ] 无 TODO/占位符/编造标记，`status` 诚实，缺数据标 `partial/failed/insufficient_evidence`（异常处理）
- [ ] `python -B scripts/selftest.py` 全过：同输入两次产物逐字节一致（确定性/可靠体验）

## 6. 降级策略

- 缺材料 / 只有摘要 / 全文不可得：如实记入 `uncertainty.limitations`，只写“摘要表明”，不得补写正文细节。
- 缺软件（`pdf-text-tool/ocr/latex/office-renderer/font-discovery` 不可用）：标准库脚本仍可读纯文本；标注“预览未生成/依赖不可用”，不伪造渲染文件。
- 模板版本不明或规格冲突：全部列入 `conflicting_evidence`，正文用“存在争议/尚不明确”，不偏向单边。
- 字体缺失：优先 Liberation Serif，不可得时 `degradations` 登记并用回退字体，不谎称嵌入。
- 无法校验来源真实性：`sources` 标 `verification="unverified"`，`pending_human` 登记核验路径。
- 硬性失败（缺 `case_id`/缺字段/JSON 无效）：`main.py verify` 退出码 2，先修复再继续。
- 任何情况禁止编造评测分数、运行结果、文献元数据或科学数据。

## 脚本与资源

- `python -B scripts/main.py scaffold|verify|seal|digest`：骨架落盘（幂等增量合并）/ 契约+摘录可核验自检 / 确定性封印 / 打印内容摘要。
- `python -B scripts/selftest.py`：空输入、坏 JSON、坏编码、摘录定位、确定性五项自检。
- `references/format-checklist.md`：A–E 可勾选格式检查清单与证据强度口径。
- `references/evidence-contract.md`：论断—来源记录结构与确定性约定。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用，`scripts/core_impl.py` 为共享实现。
