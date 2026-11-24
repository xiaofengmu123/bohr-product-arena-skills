---
name: fact-traceable-academic-writing
description: >
  在给定科研材料（事实底稿、实验记录、图表、参考文献）上撰写、改写、校对学术论文章节，
  为每个数值、单位、样本量、图注与引用保留 locator + 逐字 quote，产出可编辑稿件、证据映射
  与可复核的 result.json。本 Skill 以"输出契约完整对齐 + change_log 全程留痕"为核心：
  result.json 原样回填 case.json 的 output_contract 全部字段（缺值给 null/[]，不改名不删键），
  每次改动追加 ts/step/note；适用于学术写作的生成/改写/校对/格式/引用/双语/结构化写作任务。
version: 2.0.0
---

# 学术论文写作（fact-traceable academic writing）

## 1. 角色与何时使用

本 Skill 把通用模型约束为"事实可追溯、契约可复核"的学术写作引擎：只在给定材料内取事实，
逐条标来源与引用，先落盘可编辑的 partial 结果，再增量改进。

- **使用**：`/input/case.json` 的任务属于学术写作的生成/改写/校对/格式/引用/双语/结构化
  写作，且目标是产出可编辑学术产物时触发。
- **不使用**：纯文献检索、无材料凭空写作、仅问答、与学术写作无关的通用任务。
- **相对基础 Agent 的增益**：输出契约逐键对齐、change_log 留痕、证据映射（locator+quote）、
  悬空来源/悬空引用检测、缺项显式标注、确定性可复跑。

## 2. 输入契约

- 运行时读取 `/input/case.json`（任务说明、`output_contract`、材料清单与约束）与
  `/input/task.md`（题目与评分/运行约定）；材料在 `/input` 下按题面提供。
- PDF 用 `pdftotext` 真实解析，图片/表格用 OCR/渲染工具，**不依据文件名或领域标签补造数据**。
- 中间产物写 `/app/work/`，最终可编辑产物与 `result.json` 写 `/app/output/`。
- 脚本只用 Python 标准库，在 Skill 根目录（`/skills/weekly-skill`）下用相对路径调用：
  `python3 scripts/main.py`、`scripts/evidence.py`、`scripts/selftest.py`。

## 3. 输出契约（本版核心：契约对齐 + change_log）

必须产出 `/app/output/result.json`，顶层同时含两类键：

1. **output_contract 全部字段**：从 case.json 的 `output_contract` 原样回填全部键，缺值给
   `null`/`[]`/`{}`，不改名、不删键、不新增同义键；case.json 未定义时默认
   `deliverable`、`fact_source_map`、`version_limits`。
2. **标准字段**：`case_id`（来自 case.json 的 id/case_id，缺省用文件名）、`status`
   （`in_progress|partial|complete|failed`，与实际完成度一致）、`sources`（每条事实/引用：
   ref_id + locator + 逐字 quote）、`uncertainty`、`pending_human`、`change_log`、
   `errors`、`degradations`、`runtime`。

主稿件为可编辑 Markdown `/app/output/manuscript.md`（保留层级、图注编号、引用格式）；要求
docx/xlsx/pptx 时另渲染 PDF/PNG 预览。事实性断言用 `[S:来源ref_id]` 标来源、`[C:键]` 标
引用，文末用 `[C:键]: 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义条目。详细 schema 见
`references/conventions.md`。

## 4. 执行流程（预算 900 秒 / 64 次请求 / 单次 ≤16384 token）

**硬规则：前 3 分钟内必须先落盘 result.json 骨架与材料索引，之后只做增量改进；任何阶段
不得编造数据。**

- **Step 0 读取与骨架**（0–3 分钟，约 4–8 次请求）
  读 `/input/case.json`、`task.md`；执行
  `python3 scripts/main.py init --input /input --output /app/output --work /app/work`
  落盘 `/app/output/result.json`（status=in_progress，契约全键）、
  `/app/work/contract_meta.json`、`/app/work/material_index.json`。
- **Step 1 材料索引与证据抽取**（3–15 分钟，约 10–20 次请求）
  执行 `python3 scripts/main.py index --input /input --work /app/work`；逐材料读取，抽取
  关键断言、数值、单位、样本量、图注、文献条目，写回 material_index.json 的
  `key_claims`/`citations`（每条带 locator+quote），记录冲突与缺失。
- **Step 2 撰写/改写稿件**（15–30 分钟，约 15–25 次请求）
  按 output_contract 与学科模板写 `/app/output/manuscript.md`；每个事实/数字/引用挂
  `[S:]`/`[C:]`；缺项标 `[缺失]`/`[待确认]`，不硬填；每次改动跑
  `python3 scripts/main.py log --step draft --note "..."`。
- **Step 3 一致性复核**（30–40 分钟，约 8–12 次请求）
  执行 `python3 scripts/evidence.py --draft /app/output/manuscript.md --result /app/output/result.json --index /app/work/material_index.json`，
  再执行 `python3 scripts/selftest.py --input /input --output /app/output --work /app/work`，
  修正悬空来源、悬空引用、缺项、图/表编号冲突。
- **Step 4 收口**（40–50 分钟，约 5–8 次请求）
  执行 `python3 scripts/main.py finalize --input /input --output /app/output --work /app/work --status complete|partial`，
  合并 change_log、复检契约全键、写 runtime；渲染预览；留 5–10 分钟余量处理异常。

（时间与请求数为预算示意；核心原则"先落盘、再增量、终复核"不可违反。）

## 5. 自检清单（逐条对应评分维度）

- [ ] result.json 含 case_id + output_contract 全字段 + status/sources/uncertainty/pending_human/change_log/errors/degradations
- [ ] 稿件是完整可编辑章节稿，不是提纲、写作建议或素材堆叠
- [ ] 每个数值/单位/样本量/图注/作者/年份/期刊/DOI 可在 sources 或 material_index 回溯（locator+quote）
- [ ] 每条 `[C:键]` 在文末定义且支撑所在句；无装饰性、无错配引用
- [ ] 关键章节、图表、参考文献、声明字段齐备，缺项已显式标注
- [ ] 摘要与结果、正文与图注、方法与步骤无冲突；待核清单已输出
- [ ] change_log 记录每次改动（ts/step/note），无静默覆盖
- [ ] 未确认/冲突/缺软件/缺数据已如实写入 uncertainty 与 degradations，无编造

完整清单（含逐项处置动作）见 `references/checklist.md`；脚本结果以 `selftest.py` 为准。

## 6. 降级策略

- 缺 PDF 解析/OCR：用可用工具；失败则把材料标 `[无法解析]`，不凭文件名或标签补写。
- 文献取不到全文：仅用给定元数据引用，缺失字段写 `unknown`，不杜撰 DOI/卷期/页码。
- 材料冲突：并列各方 + 来源，标 `[冲突待确认]`；需要取舍时显式声明依据。
- 缺图注/材料超长/格式不认识：按可用部分产出 partial 结果，status=partial，写入 limitations。
- 脚本运行失败：保留已落盘骨架，手工补字段，失败原因如实写入 errors/degradations。
- 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence` 或 partial 结果，不崩溃。

## 7. 脚本速查（相对路径）

```bash
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/main.py index --input /input --work /app/work
python3 scripts/main.py log --step draft --note "write results section"
python3 scripts/evidence.py --draft /app/output/manuscript.md --result /app/output/result.json --index /app/work/material_index.json
python3 scripts/selftest.py --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work --status complete
```

确定性要求：脚本固定随机种子与排序，同一输入重复运行结果一致；先写 partial 再改进。
