---
name: academic-paper-writing
description: >
  在给定科研材料（事实底稿、实验记录、图表、参考文献）上撰写、改写、校对或结构化学术论文章节，
  保证数值、单位、样本量、图注与来源一致，每条引用可回溯，缺项与冲突显式标注，产出可编辑稿件
  与可逐条复核的 result.json。适用于学术写作的生成/改写/校对/格式/引用/双语/结构化写作任务。
version: 1.1.0
---

# 学术论文写作 Skill（fact-traceable academic writing）

## 1. 角色与何时使用

本 Skill 把 Agent 约束为「事实可追溯的学术写作引擎」：只在给定科研材料内取事实，逐条标注来源
（`[S:材料ID]`）与引用（`[C:键]`），产出可编辑、可逐条复核的章节稿，而不是提纲、写作建议或素材堆叠。

- **使用**：`/input/case.json` 的任务属于学术写作的生成/改写/校对/格式/引用/双语/结构化写作时触发。
- **不使用**：纯文献检索、无材料凭空写作、仅问答、与学术写作无关的通用任务。
- **增益**：材料索引、证据映射（locator + 逐字 quote）、事实-引用-章节三向对齐、缺项/冲突检测、改动留痕。

## 2. 输入契约

- 运行时读取 `/input/case.json`（任务说明、`output_contract`、材料清单与约束）与 `/input/task.md`（题目与运行约定）。
- 科研材料按题面提供；PDF 需真实解析（`pdftotext`），图片/表格用渲染或 OCR 工具，**不依据文件名或领域标签补造数据**。
- 中间产物写 `/app/work/`；最终可编辑产物与 `result.json` 写 `/app/output/`。
- 脚本只用 Python 标准库，相对路径调用：`scripts/main.py`；可用 `python3`（缺则 `python`）。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层键：

- `case_id`：取 `/input/case.json` 的 `case_id`/`id`，缺省用文件名；
- `output_contract` **全部字段**：原样回填全部键（缺值给 `null`/`[]`），不改名、不删未知键；未定义时默认 `deliverable`、`fact_source_map`、`version_limits`；
- `status`：`in_progress | partial | complete | insufficient_evidence | input_error | failed`，与实际完成度一致；
- `sources`：每条事实/引用的来源，形如 `{"ref_id","locator","quote","role"}`（locator=材料位置，quote=逐字原文）；
- `uncertainty`：无法确认、冲突、缺失、需人工确认项；`pending_human`：需人工裁决项；
- `change_log`：每次改动 `{"ts","step","note"}`。

主稿件写 `/app/output/manuscript.md`（保留章节层级、图注编号、引用格式）。要求 docx/xlsx/pptx 时另渲染真实 PDF/PNG 预览。事实断言用 `[S:材料ID]` 标来源，引用用 `[C:键]`，文末 `[C:键]: 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义。证据映射另写 `/app/output/evidence_map.json`。

## 4. 执行流程（预算 900 秒 / 64 次请求 / 单次 ≤16384 token）

**硬规则：前 3 分钟内必须落盘一版 partial 结果骨架，之后只做增量改进；任何阶段不得编造数据。**

- **Step 0 读取与骨架（0–3 分钟）**
  读 `/input/case.json`、`/input/task.md`；执行
  `python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work`
  → 产出 `/app/output/result.json`（status=`in_progress`）、`/app/work/material_index.json`、`/app/work/contract_meta.json`。
- **Step 1 材料索引与证据抽取（3–15 分钟）**
  逐材料读取，抽取关键断言、数值、单位、样本量、图注、文献条目，写回 `material_index.json` 的 `key_claims/numbers/citations/flags`；每条记 locator + 逐字 quote；记录冲突与缺失。
- **Step 2 撰写/改写稿件（15–30 分钟）**
  按 `output_contract` 与学科模板写 `/app/output/manuscript.md`；每个事实/数字/引用挂 `[S:]`/`[C:]`；缺项标 `[缺失]` 或 `[待确认]`，不硬填。
- **Step 3 一致性复核（30–40 分钟）**
  执行 `python3 scripts/main.py verify --input /input --output /app/output --work /app/work`，
  按 `/app/work/validation_report.json` 修正悬空来源、悬空引用、缺项、图表编号冲突；重跑至退出码 0。
- **Step 4 收口（40–50 分钟）**
  汇总 deliverable / fact_source_map / version_limits，填写 status/sources/uncertainty/pending_human/change_log，渲染预览；留 5–10 分钟余量处理异常。

（时间与请求数为预算示意；核心原则「先落盘、再增量、终复核」。）

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] result.json 合法，含 case_id + output_contract 全字段（已填充）+ status/sources/uncertainty/pending_human/change_log
- [ ] 稿件是完整可编辑章节稿，不是提纲、写作建议或素材堆叠
- [ ] 每个数值/单位/样本量/图注/作者/年份/期刊/DOI 可在 material_index 或文献条目回溯
- [ ] 每条 `[C:键]` 文末定义且支撑所在句；无装饰性、无错配引用
- [ ] 关键章节、图表、参考文献、数据可得性声明齐备，缺项已显式标注
- [ ] 摘要与结果、正文与图注、方法与步骤无冲突；待核清单已输出
- [ ] change_log 记录每次修改；`main.py verify` 退出码 0 且 validation_report 全 pass
- [ ] 未确认/冲突/缺软件/缺数据已如实写入 uncertainty 与 limitations，无编造

完整清单（含逐项处置动作）见 `references/checklist.md`；学科模板见 `references/template.md`。

## 6. 降级策略

**缺什么标什么，绝不编造。**

- 缺 PDF 解析/OCR：用可用工具；失败则把该材料标 `[无法解析]`，不凭文件名或标签补写。
- 文献取不到全文：仅用给定元数据引用，缺失字段写 `unknown`，禁止杜撰 DOI。
- 材料冲突：并列各方 + 来源，标 `[冲突待确认]`；需要取舍时显式声明依据。
- 缺图注 / 材料超长 / 格式不认识：按可用部分产出 partial 结果，status=`partial`，写入 limitations。
- 空输入：写 `insufficient_evidence`；坏 JSON/坏编码：捕获记录到 errors/degradations，继续处理其余材料。
- 脚本运行失败：保留已落盘骨架，手工补字段，如实记录失败原因到 change_log。

## 7. 边界

- 一个 Skill 只解决「材料 → 可编辑学术章节稿」一类任务；不做通用大而全系统。
- 没有真实材料时输出「材料不足」，不用示例填充；不虚标 `complete`。
- 交付前在本机跑 `python3 scripts/selftest.py` 验证脚本可运行（不产出正式结果）。
