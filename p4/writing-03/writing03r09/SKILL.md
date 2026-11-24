---
name: academic-template-adaptation
description: 学术格式与模板适配 Skill。当任务要求依据给定科研论文材料完成学术写作、改写、校对、引用、双语或结构化写作，并把成品套用到目标模板（栏数、字号、行距、页边距、标题层级、图表题注、参考文献样式、导出格式），同时为每条实质论断保留“原文摘录—来源定位—证据强度—不确定性”可回溯证据链、最终产出 /app/output/result.json 时使用。
version: 1.0.0
---

# 学术格式与模板适配 Skill

## 1. 角色与何时使用

- 角色：模板适配执行器与可复核质检器。目标不是“多写”，而是让成品同时满足两条：格式规格逐项可核对，内容事实逐句可回溯。
- 触发：`/input/case.json` 要求依据给定科研材料完成写作、改写、校对、引用、格式或结构化写作，并套用目标模板或格式约束时。
- 不适用：纯创意写作、无来源要求的闲聊扩写、超出给定材料的领域外推测。

## 2. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、任务说明、材料清单、`output_contract`、评分证据范围）与 `/input/task.md`（题面与运行时约定）。
- 科研材料：由 `case.json` 指明的论文/文档。PDF 用 `pdf-text-tool` 抽全文（必要时 `ocr-tool`、`office-renderer`、`latex-compiler`）；实际文件内容是唯一事实依据，不按文件名或领域标签脑补。
- 工作目录：`/app/work`（中间产物、抽取文本）；最终结果与可编辑产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。

## 3. 输出契约

必产 `/app/output/result.json`。字段 = `case_id` + `case.json` 的 `output_contract` 全部字段（原样回填，缺值给 `null`/`[]`，不改名、不删未知键）+ 以下保留字段：

- `status`：`partial | complete | insufficient_evidence | failed | input_error`，诚实标注。
- `deliverable`：主产物（`content` 描述 + `artifact_path` 指向可编辑产物与 pdf/png 预览）。
- `fact_source_map`：每条论断 `claim_id/claim_text/quote/locator/source_ref/evidence_level/status`。
- `version_boundary`：修改差异、人工确认项、材料缺口与异常。
- `sources`：`id/type/title/path/doi/accessed/locator`；无法核验标 `verification="unverified"`。
- `uncertainty`：`claims_requiring_confirmation`、`conflicting_evidence`、`material_gaps`、`limitations`。
- `pending_human`：需人工确认项清单。
- `change_log`：`[{ts, action, detail}]`，每次落盘追加一条。
- 另附 `errors`、`degradations`、`runtime`。

若 `case.json` 无 `output_contract`，用三段式兜底：`deliverable` + `fact_source_map` + `version_boundary`。每个案例还须产出可编辑产物（md/tex/docx）与渲染预览（pdf/png）到 `/app/output/`，并把路径回填 `deliverable.artifact_path`。

## 4. 执行流程（900 秒 / 64 请求）

硬约束：**前 3 分钟（180 秒）内必须先落盘一版 `result.json` 骨架，之后只做增量改进，不得整体重建。**

- 阶段 0 盘点（0–60s，≤2 请求）：读 `case.json` + `task.md`，解析 `case_id`、`output_contract`、材料清单，列 `/input` 文件。
- 阶段 1 骨架落盘（≤180s 前，≤1 请求）：`python scripts/main.py scaffold`，确认 `case_id` 与契约字段齐全，`status=partial`。
- 阶段 2 模板规格与证据提取（60–300s，≤20 请求）：从模板/格式要求抽取可核对规格（栏数、字号、行距、页边距、标题层级、图表题注、参考文献样式、导出格式）形成检查项清单；从全文抽取逐字可引用片段（保留章节/段落/页码定位），切分论断单元。
- 阶段 3 来源匹配与强度分级（180–540s，≤20 请求）：每条论断配 `source_ref`，校验“原文是否支持该句”；标 `evidence_level`；写不确定性限定语；冲突证据并列，不隐藏。
- 阶段 4 成稿与可编辑产物（360–780s，≤15 请求）：按体裁/受众/格式套用模板写 `deliverable.content`；保存 md/tex/docx + pdf/png 预览；回填 `artifact_path`。
- 阶段 5 自检与收尾（720–900s，≤6 请求）：`python scripts/main.py verify`；修复硬性错误；写 `status/uncertainty/pending_human/change_log`；预留 ≤2 请求重试。

每步增量写回 `result.json` 并追加 `change_log`；任一步超时或请求耗尽时保持 `status=partial`，如实写 `limitations`，绝不编造。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] `case_id` 与 `/input/case.json` 一致（任务完成度）
- [ ] `result.json` 含 `output_contract` 全部字段 + `status/sources/uncertainty/pending_human/change_log`（结果完整性）
- [ ] 每条实质论断有 `claim_id/claim_text/quote/source_ref/evidence_level`（找得到/对得上）
- [ ] 每条 `quote` 经 `main.py verify` 校验能在材料全文定位，未定位已标 `limitations`（可核验性）
- [ ] 引用真实、元数据准确，排除二手转引/撤稿/版本差异（正确性）
- [ ] 证据强度与表述相称：相关≠因果、单篇小样本≠共识、冲突证据未隐藏（说得准）
- [ ] 数字/术语/图表/引用逐条有来源，未新增材料外事实（事实与来源映射）
- [ ] 未确认项、缺口、异常写入 `version_boundary/uncertainty/pending_human`（版本与边界）
- [ ] 可编辑产物 + pdf/png 预览已产出并回填路径（任务完成度）
- [ ] 无 TODO/占位符/编造标记，`status` 诚实，缺数据标 `partial/failed`（可靠体验）
- [ ] 复现路径完整：论断编号 → 摘录 → 定位 → 强度理由可独立回查（复得现）
- [ ] 模板检查项逐条有“规格值—成品实测值—符合性”记录（排得对/转得全/交得出）

## 6. 降级策略

- 缺材料/只有摘要/全文不可得：记入 `uncertainty.limitations`，只写“摘要表明”，不补正文细节。
- 缺软件（`pdf-text-tool/ocr/latex/office-renderer` 不可用）：标准库脚本仍读纯文本；标注“预览未生成/依赖不可用”，不伪造渲染文件。
- 原文与摘要矛盾、证据冲突：全部列入 `conflicting_evidence`，正文用“存在争议/尚不明确”。
- 无法核验来源真实性：`sources` 标 `verification="unverified"`，`change_log` 记录核验路径。
- 硬性失败（缺 `case_id`/字段/JSON 无效）：`main.py verify` 退出码 2，先修复再继续。
- 字体缺失：优先使用组织方提供的 Liberation Serif；缺失字体记入 `degradations`，不擅自替换并谎称嵌入。
- 任何情况禁止编造评测分数、运行结果、文献元数据或科学数据。

## 脚本与资源

- `python scripts/main.py scaffold`：解析 case.json 并落盘幂等 partial 骨架（增量合并）。
- `python scripts/main.py verify`：契约 + 摘录可核验性 + 无占位符自检，产出 `verification_report.json`。
- `python scripts/selftest.py`：空输入/坏 JSON/缺字段/重复运行自检，退出码 0 表示通过。
- `references/evidence-rubric.md`：证据强度分级、不确定性口径与论断—来源记录结构。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用。
