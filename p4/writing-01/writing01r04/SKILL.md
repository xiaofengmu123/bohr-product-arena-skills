---
name: academic-paper-writing
description: >
  在给定科研材料（事实底稿、实验记录、图表、参考文献）基础上撰写、改写、校对、格式化或结构化改写学术论文章节。
  保证数值、单位、样本量、图注与来源逐条可回溯，每条引用支撑其所在论断，缺项/冲突/无法解析处显式标注，
  产出可继续编辑的稿件（manuscript.md）与可复核的 result.json。适用于学术写作的生成、改写、校对、格式、
  引用、双语或结构化写作任务。
version: 1.1.0
---

# 学术论文写作（fact-traceable academic writing）

## 1. 角色与何时使用

本 Skill 把通用模型约束为"事实可追溯的学术写作引擎"：只在 `/input` 给定的科研材料与真实文献条目内取事实，逐条挂来源与引用，产出可编辑、可逐条复核的稿件，缺项如实标注，绝不凭领域常识或文件名补造数据。

- **何时使用**：`/input/case.json` 属于学术写作的生成、改写、校对、格式、引用、双语或结构化写作任务时触发。
- **不使用**：无材料的凭空写作、纯文献检索、仅问答、与学术写作无关的通用任务。
- **相对基础 Agent 的增益**：材料索引、证据映射表（claim→locator→逐字 quote）、事实-引用-章节三向对齐、缺项/冲突检测、一致性待核清单、改动留痕、降级台账。

## 2. 输入契约

- 运行时读取 `/input/case.json`（任务说明、`output_contract`、材料清单与约束）与 `/input/task.md`（题目、评分/运行约定）。
- 材料以题面实际文件为准：PDF 用 `pdftotext` 真实解析，图片/表格用 OCR/渲染工具，不依据文件名或领域标签补造内容。
- 中间产物写 `/app/work/`；最终可编辑产物与 `result.json` 写 `/app/output/`。
- 脚本只用 Python 标准库，在 Skill 根目录 `/skills/weekly-skill` 下以相对路径调用（如 `python3 scripts/main.py ...`），路径以命令行参数传入。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层键：

- `case_id`：来自 `/input/case.json` 的 `id`/`case_id`，缺省用文件名；
- `output_contract` 的**全部字段**原样回填（不改名、不删未知键、缺值给 `null`）；未定义时默认 `deliverable`、`fact_source_map`、`version_limits`；
- `status`：`in_progress | partial | complete | insufficient_evidence | input_error | failed`，与实际完成度一致，缺项不得虚标 `complete`；
- `sources`：每条事实/引用的证据（`id`、`claim`、`ref_id`、`locator`、逐字 `quote`、`cite_key`、`status`）；
- `uncertainty`：无法确认、冲突、缺失、需人工确认项（含 `limitations` 子项）；
- `pending_human`：需要作者人工确认的事项；
- `change_log`：每次改动的 `ts`/`step`/`note`；
- 另含 `errors`、`degradations`、`outputs`、`runtime`。

主稿件作为可编辑 Markdown 写 `/app/output/manuscript.md`（保留章节层级、图注编号、引用格式）；要求 docx/xlsx/pptx 时另渲染 PDF/PNG 预览。正文事实断言用 `[S:材料ID]` 标来源、`[C:键]` 标引用，文末用 `[C:键] 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义文献条目。详细 schema 见 `references/conventions.md`。

## 4. 执行流程（预算 900 秒 / 64 次模型请求 / 单次 ≤16384 token）

**硬规则：前 3 分钟内必须先落盘一版 `result.json` 骨架（status=partial 或 in_progress）与材料索引，之后只做增量改进；任何阶段不得编造数据。**

| 步骤 | 输入 | 输出 | 时间/请求预算 |
|---|---|---|---|
| Step 0 骨架（≤3 分钟） | case.json、task.md | work/material_index.json、work/evidence_map.json、work/contract_meta.json、output/result.json（partial） | 4–8 次请求 |
| Step 1 材料索引与证据抽取 | 逐材料读取 | 回写 material_index.json 的 key_claims/numbers/citations/flags，写 evidence_map.json | 10–20 次请求 |
| Step 2 撰写/改写稿件 | 索引+证据 | output/manuscript.md（每个事实/数字/引用挂 `[S:]`/`[C:]`，缺项标 `[缺失]`/`[待确认]`） | 15–25 次请求 |
| Step 3 一致性复核 | 稿件+索引+证据 | 修复悬空来源/引用、图表编号冲突、缺节 | 8–12 次请求 |
| Step 4 收口（留 5–10 分钟余量） | 全部中间产物 | 汇总 status/sources/uncertainty/change_log，渲染预览 | 5–8 次请求 |

- Step 0：执行 `python3 scripts/main.py --step init --input /input --output /app/output --work /app/work`。
- Step 3：执行 `python3 scripts/consistency_check.py --draft /app/output/manuscript.md --index /app/work/material_index.json --evidence /app/work/evidence_map.json --out /app/work/check_report.json`，按报告修正；如 agent 直接改动了 index/evidence，用 `python3 scripts/main.py --step record --work /app/work --note "..."` 留痕。
- Step 4：执行 `python3 scripts/main.py --step finalize --input /input --output /app/output --work /app/work [--status complete|partial|...]` 收口。
- 自检：`python3 scripts/selftest.py`（合成输入冒烟测试，不触碰正式案例）。

## 5. 自检清单（逐条对应评分维度，提交前逐项勾选）

- [ ] result.json 含 `case_id` + output_contract 全字段 + `status`/`sources`/`uncertainty`/`pending_human`/`change_log`，可被 `json.load` 读取
- [ ] 稿件是完整可编辑章节稿，不是提纲、写作建议或素材堆叠（任务完成度）
- [ ] 每个数值/单位/样本量/统计显著性/图注/作者/年份/期刊/DOI 可在 evidence_map 或文献条目逐字回溯（正确性）
- [ ] 每条 `[C:键]` 在文末定义且支撑所在句，无装饰性、无错配引用（引用对应）
- [ ] 关键章节、图表、参考文献、数据可得性声明齐备，缺项已显式标注（完整性）
- [ ] 摘要与结果、正文与图注、方法与步骤无冲突；`check_report.json` 无未修复项（可核验性）
- [ ] `change_log` 记录从骨架到成稿的每次改动；重复运行结果一致（留痕/稳定性）
- [ ] 未确认/冲突/缺软件/缺数据已如实写入 `uncertainty` 与 `limitations`，无编造（降级诚实）
- [ ] 空输入/坏 JSON/坏编码/缺参数/超长文件均不崩溃，退出码 0 且产出合法 partial 结果

完整处置动作见 `references/checklist.md`。

## 6. 降级与不确定性策略（本版重点）

**总原则：缺什么标什么，绝不编造；无法产出的部分写 `partial`，并在 `uncertainty`/`limitations`/`change_log` 中说明。**

| 情况 | 处置 |
|---|---|
| PDF 解析工具缺失/失败 | 用可用工具提取；失败则材料标 `[无法解析]`，不凭文件名/标签补写内容 |
| 图表缺图注 | 图注标 `[缺失]`，正文相应描述标 `[待确认]`，计入 `uncertainty` |
| 文献取不到全文 | 仅用给定元数据引用；缺失字段写 `unknown`/`not_reported`，禁止补 DOI |
| 材料间数值/口径冲突 | 并列各方+来源，标 `[冲突待确认]`，计入 `uncertainty` 的 `source_conflict` |
| 材料超长/格式不认识 | 按可用部分产出 `partial`，说明未处理部分及原因 |
| 空输入目录 | 退出码 0，写 `insufficient_evidence` 结果并列出缺口 |
| 坏 JSON/坏编码/缺参数 | 捕获记录到 `errors`/`degradations`，继续处理其余材料 |
| 脚本运行失败 | 保留已落盘骨架，手工补字段，如实记录失败原因 |

所有 `[缺失]`/`[待确认]`/`[冲突待确认]`/`[无法解析]` 标记必须在 `uncertainty` 中有对应条目；需要作者定夺的写 `pending_human`。
