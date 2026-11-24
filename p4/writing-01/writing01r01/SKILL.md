---
name: academic-paper-writing
description: >
  在给定科研材料（事实底稿、实验记录、图表、参考文献）上完成学术论文的生成、改写、校对、
  格式、引用、双语或结构化写作。核心保证：每个数值/单位/样本量/图注/关键结论都以
  [S:claim_id] 溯源到材料索引中的 locator + 逐字 quote，每条 [C:key] 引用可回溯且真正
  支撑所在句，缺项显式标注，产出可继续编辑的稿件与可复核的 result.json。
  适用于学术写作类任务：写得实、引得对、全得起来、改得动。
version: 2.0.0
---

# 学术论文写作（fact-traceable academic writing）

## 1. 角色与何时使用

本 Skill 把通用模型约束为"事实可追溯的学术写作引擎"：只在给定科研材料内取事实，逐条标注
来源与引用，产出可编辑、可逐条复核的学术稿件。

- **何时使用**：当 `/input/case.json` 的任务属于学术写作的生成/改写/校对/格式/引用/双语/
  结构化写作，且目标是产出可编辑学术产物时触发。
- **不使用**：纯文献检索、无材料凭空写作、仅问答、与学术写作无关的通用任务。
- **相对基础 Agent 的增益**：材料索引、证据映射表（locator + 逐字 quote）、事实-引用-章节
  三向对齐、缺项检测、一致性待核清单、改动留痕。

## 2. 输入契约

- 运行时读取 `/input/case.json`（任务说明、output_contract、材料清单与约束）与
  `/input/task.md`（题目与评分/运行约定）。
- 科研材料按题面提供，可能在 `/input/` 或 `/input/materials/` 子目录；PDF 需真实解析
  （`pdftotext`/渲染/OCR），图片表格用 OCR/渲染工具，**不依据文件名或领域标签补造数据**。
- 中间产物写 `/app/work/`；最终可编辑产物与 `result.json` 写 `/app/output/`。
- 本 Skill 脚本只用 Python 标准库，在 Skill 根目录（`/skills/weekly-skill`）下用相对路径
  `scripts/main.py` 调用，路径参数以命令行传入。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层键 = `case_id` + **output_contract 全部字段** +
固定附加字段（详见 `references/conventions.md`）：

- `case_id`：来自 `/input/case.json` 的 `id`/`case_id`，缺省用文件名；
- output_contract 字段：以 case.json 的 `output_contract` 为准；未定义时默认 `deliverable`、
  `fact_source_map`、`version_limits`；
- `status`：`complete | partial | insufficient_evidence | input_error | failed`，与实际完成度一致；
- `sources`：每条事实/引用的来源（材料位置 + 文献条目）；
- `uncertainty` / `pending_human`：无法确认、冲突、缺失、需人工确认项；
- `change_log`：每次改动的 ts/step/note。

主稿件作为可编辑 Markdown 写 `/app/output/manuscript.md`（保留层级、图注编号、引用格式）；
要求 docx/xlsx/pptx 时另渲染真实 PDF/PNG 预览。**每个事实性断言用 `[S:claim_id]` 标来源、
`[C:key]` 标引用**，文末用 `[C:key]` 定义文献条目。完整 schema 见 `references/conventions.md`。

## 4. 执行流程（预算 900 秒 / 64 次请求 / 单次 ≤16384 token）

**硬规则：前 3 分钟内必须先落盘 result.json 骨架与材料索引，之后只做增量改进；任何阶段
不得编造数据。**

- **Step 0 读取与骨架**（0–3 分钟，约 4–8 次请求）
  读取 `/input/case.json`、`task.md`；执行
  `python3 scripts/main.py init --input /input --output /app/output --work /app/work`
  产出 `/app/work/material_index.json`、`contract_meta.json`、`/app/output/result.json`
  （status=partial，deliverable 未产出）。空输入时脚本自动降级为 `insufficient_evidence`。

- **Step 1 材料索引与证据抽取**（3–15 分钟，约 10–20 次请求）
  逐材料读取，抽取关键断言、数值、单位、样本量、图注、文献条目，写回
  `material_index.json` 的 `claims`（**每条含 locator + 逐字 quote**）与 `citations`；
  记录 `conflicts` 与 `gaps`。quote 必须是材料原文字串，取不到就标 `[无法解析]`。

- **Step 2 撰写/改写稿件**（15–30 分钟，约 15–25 次请求）
  按 output_contract 与学科模板写 `/app/output/manuscript.md`；每个事实/数字/引用都挂
  `[S:]`/`[C:]`；缺项标 `[缺失]` 或 `[待确认]`，不硬填。

- **Step 3 一致性复核**（30–40 分钟，约 8–12 次请求）
  执行
  `python3 scripts/main.py evidence --draft /app/output/manuscript.md --index /app/work/material_index.json --work /app/work`
  再执行
  `python3 scripts/main.py check --draft /app/output/manuscript.md --index /app/work/material_index.json --result /app/output/result.json --work /app/work`
  按报告修正悬空来源/引用、缺 locator/quote、缺项、图表编号冲突。

- **Step 4 收口**（40–50 分钟，约 5–8 次请求）
  汇总 deliverable/fact_source_map/version_limits，执行
  `python3 scripts/main.py merge --result /app/output/result.json --work /app/work`
  把 evidence/check 报告合并回 result.json（自动回填 sources/uncertainty/version_limits，
  状态由 partial 提升为 complete 当且仅当无悬空、无缺 trace、无缺口且 deliverable 已产出）；
  再手工补 pending_human，渲染预览；留 5–10 分钟余量。最终执行
  `python3 scripts/selftest.py` 或 `python3 scripts/main.py selfcheck` 通过后交付。

（时间与请求数为预算示意；核心原则是"先落盘、再增量、终复核"。）

## 5. 自检清单（逐条对应评分维度）

- [ ] result.json 含 case_id + output_contract 全字段 + status/sources/uncertainty/pending_human/change_log
- [ ] 稿件是完整可编辑章节稿，不是提纲、写作建议或素材堆叠
- [ ] 每个数值/单位/样本量/图注/作者/年份/期刊/DOI 可在 material_index 或文献条目回溯
- [ ] 每条 claim 有 locator + 逐字 quote；每条 `[C:key]` 在文末定义且支撑所在句，无装饰性、无错配
- [ ] 关键章节、图表、参考文献、声明字段齐备，缺项已显式标注
- [ ] 摘要与结果、正文与图注、方法与步骤无冲突；待核清单已输出
- [ ] 改动留痕（change_log 记录每次修改）
- [ ] 未确认/冲突/缺软件/缺数据已如实写入 uncertainty/pending_human 与 limitations，无编造

完整清单（含逐项处置动作）见 `references/checklist.md`；自动校验用 `scripts/selftest.py`。

## 6. 降级策略

- 缺 PDF 解析/OCR：用可用工具；失败把该材料标 `[无法解析]`，不凭文件名或标签补写。
- 文献取不到全文：仅用给定元数据引用，缺失字段写 `unknown`，不杜撰 DOI。
- 材料冲突：并列各方 + 来源，标 `[冲突待确认]`；需取舍时显式声明依据。
- 缺图注 / 材料超长 / 格式不认识：按可用部分产出 partial，status=partial，写入 limitations。
- 脚本运行失败：保留已落盘骨架，手工补字段，如实记录失败原因到 `errors`。
- 输入为空/坏 JSON/坏编码：脚本不崩溃，写合法结果（`insufficient_evidence`/`input_error`）。

## 7. 脚本与调用（相对路径，纯标准库）

| 脚本 | 用途 |
|-|-|
| `scripts/main.py init` | Step 0 骨架落盘 |
| `scripts/main.py evidence` | Step 3 证据溯源校验（locator+quote） |
| `scripts/main.py check` | Step 3 章节/图表/引用/收口三查 |
| `scripts/main.py merge` | Step 4 合并报告到 result.json，自动回填 sources/uncertainty |
| `scripts/selftest.py` | 最小夹具自检，退出码 0 为通过 |
| `scripts/core_impl.py` | 共享实现（被 main/selftest 复用） |

详见 `references/conventions.md`（schema）、`references/checklist.md`（清单与降级）。
