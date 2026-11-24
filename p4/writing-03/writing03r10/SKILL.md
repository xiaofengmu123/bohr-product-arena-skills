---
name: format-template-adaptation
description: 学术格式与模板适配 Skill。当需要依据给定科研稿件与目标模板，完成模板规格识别、标题层级与编号、图表题注与交叉引用、参考文献格式、内容单元保真迁移与导出校验，并为每条格式判定与内容单元保留“规格项—原文摘录—定位—符合性”可回溯记录、最终产出 /app/output/result.json 时使用。
version: 1.0.0
---

# 学术格式与模板适配 Skill

## 1. 角色与何时使用

- 角色：把一份真实稿件按目标模板改到“可交付、可复核”的格式适配执行器与质检器。核心目标不是“外观相似”，而是让每一条格式判定都能被指认“要求从哪来、实测是多少、是否符合、依据在哪”。
- 触发：当 `/input/case.json` 要求完成学术写作/改稿/校对/引用/格式/双语或结构化写作，且需要保留来源、修改记录与模板规格时。
- 不适用：无模板/无格式约束的纯创意写作、无来源要求的闲聊式扩写、超出给定材料的领域外推测。

## 2. 核心方法：四段流水线 + 两份中间表示

本 Skill 与“通篇写作”型 Skill 的关键差异在于：先抽规格、再套样式、再对内容、最后验导出，每一步都落成结构化中间表示，使“认得准 / 排得对 / 转得全 / 交得出”可逐项核对。

- 阶段 A 认得准：从模板文件与格式要求中抽取**模板规格表（template profile）**。每条规格 = `spec_id / domain / spec_name / required_value / measured_value / compliance / source_locator / quote / confidence`。只记录模板明文写出的值；模板未写明的一律标 `not_specified`，不得凭经验脑补。
- 阶段 B 排得对：按规格表建立**样式映射（style map）**，逐项处理标题层级、编号、题注位置、参考文献字段顺序与作者截断、中英文混排；每项记录 `rule → applied_where → evidence`。
- 阶段 C 转得全：生成**内容单元锚定表（content-unit anchor）**。把正文、表格、图、公式、脚注、批注、修订、域代码、目录逐项登记为 `unit_id / unit_type / source_locator / quote / dest_locator / preserved / transform`，证明转换没有删除、合并、改写或图片化。
- 阶段 D 交得出：导出校验，检查文件可打开、字体嵌入、页面尺寸与页边距实测、目录与页码可用、保留可编辑源文件，并把每一项写回 `version_boundary`。

## 3. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、任务说明、材料清单、`output_contract`、评分证据范围）与 `/input/task.md`（题面与运行时约定）。
- 科研材料：由 `case.json` 指明的稿件、模板、论文或文档，位于 `/input` 及其子目录。PDF 用平台 `pdf-text-tool` 抽取全文（必要时 `ocr-tool`）；`.docx/.pptx` 可用 `scripts/core_impl.py` 以 zipfile 抽取；实际文件内容才是事实依据，不按文件名或领域标签脑补。
- 工作目录：`/app/work`（中间产物、抽取文本、规格表、锚定表）；最终结果与可编辑产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。**前 180 秒内必须先落盘一版 `result.json` 骨架（status=partial），之后只做增量改进，不得整体重建。**

## 4. 输出契约

必产 `/app/output/result.json`，顶层字段 = `case_id` + `case.json` 的 `output_contract` 全部字段（原样回填，缺值给 `null/[]`，不改名不删未知键）+ 以下保留字段：

- `status`：`partial | complete | failed`，诚实标注。
- `deliverable`：主产物（若 contract 已有则合并）：`content / artifact_path / preview_path`。
- `fact_source_map`：`[{claim_id, claim_text, quote, locator, source_ref, evidence_level}]`，每条实质论断可回溯。
- `version_boundary`：`{change_diff, human_confirmation, material_gaps, anomalies}`。
- `sources`：`[{id, type, title, path, doi, accessed, locator, verification}]`。
- `uncertainty`：`{claims_requiring_confirmation, conflicting_evidence, material_gaps, limitations}`。
- `pending_human`：需要人工确认/决策的事项清单。
- `change_log`：`[{ts, action, detail}]`，每次落盘追加一条。

若 `case.json` 无 `output_contract`，用三段式兜底：`deliverable`、`fact_source_map`、`version_boundary`（见上文）。每个案例还需产出可编辑产物（md/tex/docx）及其渲染预览（pdf/png）到 `/app/output/`，并把路径回填进 `deliverable`。

## 5. 执行流程（900 秒 / 64 请求）

硬约束：前 180 秒先落盘 partial 骨架，之后只增量改进。

- 阶段 0 盘点（0–60s，≤2 请求）：读 `case.json` + `task.md`，解析 `case_id`、`output_contract`、材料清单，`ls /input`。输出：任务理解 + 材料清单。
- 阶段 1 骨架落盘（≤180s，≤1 请求）：运行 `python scripts/main.py scaffold --case /input/case.json --output /app/output --work /app/work`，确认 `case_id` 正确、contract 字段齐全、`status=partial`。
- 阶段 2 认得准·规格抽取（60–300s，≤20 请求）：运行 `python scripts/main.py inspect --input /input --work /app/work` 生成材料索引与规格表草稿；抽取模板规格（字号、行距、页边距、栏数、标题层级、题注位置、参考文献样式、导出格式），自动抽取的 `confidence=low` 项必须人工复核后确认。
- 阶段 3 排得对·样式映射（180–540s，≤20 请求）：逐项建立 `rule → applied_where → evidence`，每条格式判定配 `source_locator + quote`，冲突规定并列不隐藏。
- 阶段 4 转得全·内容锚定（360–780s，≤15 请求）：生成内容单元锚定表，逐项验证 `preserved=true` 或如实记录 `transform`；数字、术语、图表、引用逐条有来源，不新增材料外事实。
- 阶段 5 交得出·自检收尾（720–900s，≤6 请求）：写 `deliverable.content` 并保存 md/tex/docx 产物 + pdf/png 预览，回填 `artifact_path`；运行 `python scripts/main.py verify --result /app/output/result.json --case /input/case.json --report /app/output/verification_report.json --input /input --work /app/work`；修复硬性错误（缺字段、摘录无法定位、占位符）；写 `status/uncertainty/pending_human/change_log`；预留 ≤2 请求重试。

每一步把增量写回 `result.json` 并追加 `change_log`；任一步超时或请求耗尽时保持 `status=partial`，如实写 `limitations`，绝不编造。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] `case_id` 与 `/input/case.json` 一致（任务完成度）
- [ ] `result.json` 含 `output_contract` 全部字段 + `status/sources/uncertainty/pending_human/change_log`（结果完整性）
- [ ] 每条格式判定有 `spec_id/spec_name/required_value/measured_value/compliance/source_locator/quote`（认得准）
- [ ] 每条实质论断有 `claim_id/claim_text/quote/locator/source_ref/evidence_level`（找得到/对得上）
- [ ] 每条 `quote` 经 `verify` 校验能在材料全文定位，未定位的已标 `limitations`（结果可核验性）
- [ ] 内容单元锚定表覆盖正文/图表/公式/脚注/批注/修订/域代码/目录，`preserved` 如实（转得全）
- [ ] 导出产物可打开、字体嵌入、页码目录可用、保留可编辑源文件（交得出）
- [ ] 引用真实存在、元数据准确，排除二手转引/撤稿/版本差异（结果正确性）
- [ ] 证据强度与表述相称，冲突证据未隐藏，模板版本差异并列（说得准）
- [ ] 无 TODO/占位符/编造标记，`status` 诚实，缺数据标 `partial/failed`（异常处理）
- [ ] 复现路径完整：规格项 → 摘录 → 定位 → 符合性理由可独立回查（可复现）

## 7. 降级策略

- 缺材料 / 只有摘要 / 全文不可得：如实记入 `uncertainty.limitations`，只写“摘要表明”，不补写正文细节；`sources.verification=unverified`。
- 模板缺失或版本不明、字体缺失：标 `not_specified / not_checkable`，写 `pending_human`，不得假设默认值。
- 缺软件（`pdf-text-tool/ocr/latex/office-renderer/font-discovery` 不可用）：标准库脚本仍可读纯文本与 `.docx/.pptx` 文本；标注“预览未生成/依赖不可用”，不伪造渲染文件。
- 原文与模板冲突、多个模板版本不一致：全部列入 `uncertainty.conflicting_evidence` 与 `version_boundary`，正文标“存在冲突/版本不明”。
- 转换失败、内容无法保真：保留源文件与差异记录，`status=partial/failed`，绝不谎报成功。
- 硬性失败（缺 `case_id`/缺 contract 字段/JSON 无效）：`verify` 退出码 2，先修复再继续。
- 任何情况禁止编造评测分数、运行结果、文献元数据或科学数据。

## 脚本与资源

- `python scripts/main.py scaffold --case /input/case.json --output /app/output --work /app/work`：骨架落盘（幂等，增量合并）。
- `python scripts/main.py inspect --input /input --work /app/work`：材料索引 + 规格表/内容锚定草稿。
- `python scripts/main.py verify --result /app/output/result.json --case /input/case.json --report /app/output/verification_report.json --input /input --work /app/work`：契约 + 摘录可核验性自检（退出码 0/1/2）。
- `python scripts/selftest.py`：用临时样例自检全流程，退出码 0 表示通过。
- `references/spec-checklist.md`：规格项检查清单与符合性口径。
- `references/degradation.md`：失败类型与降级处置对照表。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用。
