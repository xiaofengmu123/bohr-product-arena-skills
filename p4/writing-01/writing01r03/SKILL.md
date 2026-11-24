---
name: fact-traced-academic-writing
description: >
  基于给定科研材料完成学术论文章节的撰写、改写、校对与格式、引用、双语、结构化写作，
  逐条保留事实与引用的可回溯来源（locator + 逐字 quote），显式标注缺失、冲突与待确认项，
  产出可编辑稿件与可复核 result.json；内置证据映射与确定性自检，同一输入重复运行产物字节一致。
version: 2.0.0
---

# 事实可追溯学术写作（fact-traced academic writing）

## 1. 角色与何时使用

本 Skill 把通用模型约束为“事实可追溯的学术写作引擎”：只在给定科研材料内取事实，逐条标注来源定位与引用键，产出可编辑、可逐条复核的稿件，并在收口前运行确定性自检。

- **何时使用**：`/input/case.json` 的任务属于学术写作的生成、改写、校对、格式、引用、双语或结构化写作时触发。
- **不使用**：纯文献综述检索、无材料凭空写作、纯问答、与学术写作无关的通用任务。
- **相对基础 Agent 的增益**：材料索引、证据映射（locator+quote）、事实—引用—章节三向对齐、缺项/冲突检测、待核清单、改动留痕、确定性自检。

## 2. 输入契约

- 运行时读取 `/input/case.json`（任务说明、`output_contract`、材料清单与约束）与 `/input/task.md`（题目与评分/运行约定）。
- 科研材料按题面提供；PDF 需真实解析（`pdftotext`），图片/表格用渲染与 OCR 工具，不依据文件名或领域标签补造数据。实际输入文件和原论文是事实依据。
- 中间产物写 `/app/work/`；最终可编辑产物与 `result.json` 写 `/app/output/`。
- 脚本只用 Python 标准库，在 Skill 根目录（`/skills/weekly-skill`）下用相对路径 `scripts/evidence_map.py` 调用；`--input-dir/--output-dir/--work-dir` 缺省分别指向 `/input`、`/app/output`、`/app/work`。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层键：

- `case_id`：来自 `/input/case.json` 的 `id`/`case_id`/`caseId`，缺省用文件名 `case.json`；
- `output_contract` **全部字段**：以 case.json 的 `output_contract` 为准原样回填（值先用 `null` 再逐字段填充）；未定义时默认 `deliverable`、`fact_source_map`、`version_limits`；
- `status`：`in_progress | partial | complete | insufficient_evidence | input_error | failed`，必须与实际完成度一致；
- `sources`：每条事实/引用的来源（材料位置 + 文献条目，带 `locator` 与 `quote`）；
- `uncertainty`：无法确认、冲突、缺失、需人工确认项；
- `pending_human`：需人工确认/补录项清单；
- `change_log`：每次改动记录，用单调递增的 `seq` + `stage` + `note`（**不写墙钟时间**，保证重复运行字节一致）。

主稿件写 `/app/output/manuscript.md`（保留层级、图注编号、引用格式）。事实性断言用 `[S:材料ID#位置]` 标来源、`[C:键]` 标引用；文末用 `- [C:键]: 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义文献条目。要求 docx/xlsx/pptx 时另渲染 PDF/PNG 预览。详细 schema 见 `references/conventions.md`。

## 4. 执行流程（预算 900 秒 / 64 次模型请求 / 单次 ≤16384 token）

**硬规则：前 3 分钟内必须先落盘 result.json 骨架与材料索引，之后只做增量改进；任何阶段不得编造数据或文献元数据。**

- **Step 0 读取与骨架（0–3 分钟，约 4–8 次请求）**
  读 `/input/case.json`、`/input/task.md`；执行
  `python3 scripts/init_result.py --input-dir /input --output-dir /app/output --work-dir /app/work`
  产出 `/app/work/material_index.json`、`/app/work/contract_meta.json`、`/app/output/result.json`（`status=in_progress`）。骨架落盘后，后续所有改动只做增量更新。
- **Step 1 材料索引与证据抽取（3–15 分钟，约 10–20 次请求）**
  逐材料读取并解析，把关键断言、数值、单位、样本量、图注、文献条目写回 `material_index.json` 的 `key_claims/numbers/citations/flags`（每条 `key_claim` 必须含 `locator` + 逐字 `quote`）；记录冲突与缺失到 `conflicts/missing`。
- **Step 2 撰写/改写稿件（15–30 分钟，约 15–25 次请求）**
  按 `output_contract` 与学科模板写 `/app/output/manuscript.md`；每个事实/数字/引用挂 `[S:]`/`[C:]`；缺项标 `[缺失]`、`[待确认]`、`[冲突待确认]`，不硬填。
- **Step 3 一致性复核（30–40 分钟，约 8–12 次请求）**
  执行 `python3 scripts/evidence_map.py --draft /app/output/manuscript.md --index /app/work/material_index.json --work-dir /app/work`，按报告修正悬空来源、悬空引用、未用引用、章节/图表编号冲突，并复核数字是否邻近 `[S:]`。
- **Step 4 收口与自检（40–50 分钟，约 5–8 次请求）**
  汇总 `deliverable/fact_source_map/version_limits`，填 `status/sources/uncertainty/pending_human/change_log`；执行 `python3 scripts/selftest.py --input-dir /input --output-dir /app/output --work-dir /app/work` 验证 schema、确定性与降级路径；渲染预览；留 5–10 分钟余量处理异常。

（时间与请求数为预算示意；核心原则是“先落盘、再增量、终复核”。）

## 5. 自检清单（逐条对应评分维度，收口前逐条勾选）

- [ ] result.json 合法，含 `case_id` + `output_contract` 全字段 + `status/sources/uncertainty/pending_human/change_log`
- [ ] `status` 与实际完成度一致，缺项用 `partial`，不虚标 `complete`
- [ ] 稿件是完整可编辑章节稿，不是提纲、写作建议或素材堆叠
- [ ] 每个数值/单位/样本量/图注/作者/年份/期刊/DOI 可在 `material_index.json` 的 `key_claim` 中回溯到 `locator`+逐字 `quote`
- [ ] 每条 `[C:键]` 在文末定义且支撑所在句，无装饰性、无错配引用；DOI/卷期缺失写 `unknown`，不杜撰
- [ ] 关键章节、图表、参考文献、声明字段齐备；缺项已显式标注 `[缺失]` 并写入 `uncertainty`
- [ ] 摘要与结果、正文与图注、方法与步骤无冲突；待核清单已输出到 `pending_human`
- [ ] `change_log` 以 `seq/stage/note` 记录从骨架到成稿的每次改动，不含墙钟时间
- [ ] `scripts/evidence_map.py` 报告无悬空来源、无悬空引用、无未用引用
- [ ] `scripts/selftest.py` 退出码 0：schema 通过、同一输入两次运行 result.json 字节一致、空输入/坏 JSON 降级通过

完整清单与逐项处置见 `references/checklist.md`。

## 6. 降级策略

- **缺 PDF 解析/OCR**：用可用工具；失败则把该材料标 `[无法解析]`，写入 `uncertainty`，不凭文件名或标签补写。
- **文献取不到全文**：仅用给定元数据引用，缺失字段写 `unknown`，禁止补 DOI。
- **材料冲突**：并列各方 + 来源，标 `[冲突待确认]` 并写入 `sources` 的 `conflict` 字段；需要取舍时显式声明依据。
- **缺图注/材料超长/格式不认识**：按可用部分产出，`status=partial`，写入 `uncertainty` 与 `limitations`，说明未处理部分及原因。
- **坏 JSON/坏编码/空输入目录**：`init_result.py` 捕获并写入 `degradations`，仍落盘合法结果（`insufficient_evidence` 或 `input_error`），退出码 0。
- **脚本运行失败**：保留已落盘骨架，手工补字段，如实记录失败原因到 `change_log`。

## 7. 确定性约定（本版设计重心）

- 所有 JSON 用 `sort_keys=True`、`ensure_ascii=False` 写出；材料按相对路径字典序稳定排序；`change_log` 用 `seq` 单调编号，结果内容不写墙钟时间。
- 同一 `/input` 两次运行，`result.json`、`material_index.json`、`evidence_map.json` 必须字节一致；`selftest.py` 会校验这一点。
