---
name: academic-paper-writing
description: >
  在给定科研材料（事实底稿、实验记录、图表、参考文献）基础上撰写、改写、校对学术论文章节，
  保证数值、单位、样本量、图注、结论与来源逐条一致，每条引用可回溯且真正支撑论断，缺项显式
  标注，产出可编辑稿件与可复核 result.json。适用于学术写作的生成/改写/校对/格式/引用/双语/
  结构化写作任务；强调空输入、坏 JSON、缺参数、超大文件的优雅降级，绝不编造数据。
version: 2.0.0
---

# 学术论文写作（fact-traceable academic writing）

## 1. 角色与何时使用

本 Skill 把通用模型约束为"事实可追溯的学术写作引擎"：只在给定科研材料内取事实，逐条标注
来源与引用，产出可编辑、可逐条复核的学术稿件，并以 `result.json` 交付证据映射、来源定位、
不确定性、人工确认项与改动留痕。

- **使用**：`/input/case.json` 属于学术写作的生成/改写/校对/格式/引用/双语/结构化写作，目标为可编辑学术产物。
- **不使用**：纯文献综述检索、无材料凭空写作、仅问答、与学术写作无关的通用任务。
- **相对基础 Agent 的增益**：材料索引、数值/引用自动抽取、事实-引用-章节三向对齐、缺项检测、一致性复核、改动留痕、鲁棒降级。

## 2. 输入契约

- 运行时读取 `/input/case.json`（任务说明、output_contract、材料清单与约束）与 `/input/task.md`（题目与运行约定）。
- 科研材料按题面提供；PDF 用 `pdftotext` 真实解析，图片/表格用 OCR/渲染工具，**不依据文件名或领域标签补造数据**。
- 中间产物写 `/app/work/`；最终可编辑产物与 `result.json` 写 `/app/output/`。
- 脚本只用 Python 标准库，在 Skill 根目录（`/skills/weekly-skill`）下用相对路径调用：`scripts/main.py`、`scripts/consistency.py`、`scripts/selftest.py`。
- 所有路径参数均可命令行传入，缺省为 `/input/case.json`、`/input/task.md`、`/app/output`、`/app/work`。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层键：

- `case_id`：来自 case.json 的 id/case_id，缺省用文件名；
- output_contract **全部字段**：以 case.json 的 output_contract 为准，原样回填（缺值 `null`，不删未知键）；未定义时默认 `deliverable`、`fact_source_map`、`version_limits`；
- `status`：`in_progress | partial | complete | failed | insufficient_evidence | input_error`，必须与实际完成度一致；
- `sources`：每条关键结论的来源（材料 locator + 逐字 quote + 文献条目）；
- `uncertainty`：无法确认、冲突、缺失、需人工确认项；
- `pending_human`：需作者拍板的点；
- `change_log`：每次改动的 `{ts, step, note}`；
- `errors`/`degradations`/`runtime`/`artifacts`：解析失败、降级处置、运行信息与产物路径。

主稿件作为可编辑 Markdown 写 `/app/output/manuscript.md`（保留层级、图注编号、引用格式）；要求
docx/xlsx/pptx 时另渲染真实 PDF/PNG 预览。事实性断言用 `[S:材料ID]` 标来源、`[C:键]` 标引用，
文末用 `[C:键] 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义文献。详细 schema 见 `references/conventions.md`。

## 4. 执行流程（预算 900 秒 / 64 次请求 / 单次 ≤16384 token）

**硬规则：前 3 分钟内必须先落盘 `status=partial` 结果骨架，之后只做增量改进；任何阶段不得编造数据。**

- **Step 0 读取与骨架**（0–3 分钟，4–8 次请求）
  读 `/input/case.json`、`task.md`；执行
  `python3 scripts/main.py --case /input/case.json --task /input/task.md --output /app/output --work /app/work`
  产出 `/app/work/material_index.json`、`contract_meta.json`、`/app/output/result.json`（status=partial）。
  空输入、坏 JSON、坏编码、超大文件均被脚本降级为合法产物，不崩溃。
- **Step 1 材料索引与证据抽取**（3–15 分钟，10–20 次请求）
  逐材料读取，抽取关键断言、数值、单位、样本量、图注、文献条目，回填 `material_index.json` 的
  `key_claims/numbers/citations/flags`；PDF/图片/办公文档先解析再回填；记录冲突与缺失。
- **Step 2 撰写/改写稿件**（15–30 分钟，15–25 次请求）
  按 output_contract 与学科模板写 `/app/output/manuscript.md`；每个事实/数字/引用挂 `[S:]`/`[C:]`；
  缺项标 `[缺失]` 或 `[待确认]`，不硬填；改动写入 change_log。
- **Step 3 一致性复核**（30–40 分钟，8–12 次请求）
  执行 `python3 scripts/consistency.py --draft /app/output/manuscript.md --index /app/work/material_index.json --work /app/work`，
  按报告修正悬空来源、悬空引用、缺项、图表编号冲突。
- **Step 4 收口**（40–50 分钟，5–8 次请求）
  汇总 deliverable/fact_source_map/version_limits，填 status/sources/uncertainty/pending_human/change_log，
  渲染预览；留 5–10 分钟余量处理异常。

（时间与请求数为预算示意；核心原则是"先落盘、再增量、终复核"。）

## 5. 自检清单（逐条对应评分维度）

- [ ] result.json 含 case_id + output_contract 全字段 + status/sources/uncertainty/pending_human/change_log
- [ ] 稿件是完整可编辑章节稿，不是提纲、写作建议或素材堆叠
- [ ] 每个数值/单位/样本量/图注/作者/年份/期刊/DOI 可在 material_index 或文献条目回溯
- [ ] 每条 `[C:键]` 在文末定义且支撑所在句；无装饰性、无错配引用
- [ ] 关键章节、图表、参考文献、声明字段齐备，缺项已显式标注
- [ ] 摘要与结果、正文与图注、方法与步骤无冲突；待核清单已输出
- [ ] 改动留痕（change_log 记录每次修改）
- [ ] 未确认/冲突/缺软件/缺数据已如实写入 uncertainty 与 limitations，无编造
- [ ] 鲁棒性：空输入、坏 JSON、坏编码、缺参数、超大文件均已降级且不崩溃

完整清单（含逐项处置动作）见 `references/checklist.md`；提交前运行 `python3 scripts/selftest.py`（8 个场景全过）。

## 6. 降级策略

- 空输入目录：退出码 0，写 `insufficient_evidence` 并列出缺口，不编造章节。
- 坏 JSON / 坏编码：记录到 `errors`/`degradations`，继续处理其余材料，不崩溃。
- 缺参数/未知参数：用缺省路径；未知参数被 argparse 干净拒绝（rc=2），不污染状态。
- 超大文件：按 `--max-bytes`（默认 2MB）跳过并记录，不整体失败。
- 缺 PDF 解析/OCR：用可用工具；失败则把该材料标 `[无法解析]`，不凭文件名或标签补写。
- 文献取不到全文：仅用给定元数据引用，缺失字段写 `unknown`，不杜撰 DOI。
- 材料冲突：并列各方 + 来源，标 `[冲突待确认]`；需取舍时显式声明依据。
- 写盘失败：回退到最小合法产物（回退目录或最小 JSON），不崩溃。
- 脚本运行失败：保留已落盘骨架，手工补字段，并如实记录失败原因。

## 7. 文件与维护

- 目录：`SKILL.md`、`scripts/main.py`、`scripts/consistency.py`、`scripts/selftest.py`、`references/conventions.md`、`references/checklist.md`。
- 依赖：仅 Python 标准库；不依赖作者个人文件或手工步骤；同一输入两次运行结果结构一致。
- 边界：只解决"材料→可追溯学术章节稿"这一类任务；无真实材料时输出"材料不足"，不用示例填充。
