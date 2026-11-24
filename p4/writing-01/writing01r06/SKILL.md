---
name: fact-traceable-academic-writing
description: >
  在给定科研材料（事实底稿、实验记录、图表、参考文献）基础上撰写、改写、校对学术论文章节，
  保证数值、单位、样本量、图注与来源一致，每条引用可回溯，缺项显式标注，产出可编辑稿件与可复核 result.json。
  适用于学术写作的生成、改写、校对、格式、引用、双语、结构化写作任务。
version: 2.0.0
---

# 事实可追溯的学术论文写作（中间表示 + 可复核）

本 Skill 把通用模型约束为"事实可追溯的学术写作引擎"：只在给定材料内取事实，逐条挂来源与引用，
并**以结构化中间产物（plan / material_index / figure_model / evidence_map）作为不可跳过的审计链**，
使"正文事实 → `[S:定位]` → 材料主张 → 定位符 + 逐字引用 → 源文件"全程可复核、可重跑。

## 1. 角色与何时使用

- **何时使用**：`/input/case.json` 的任务属于学术写作的生成、改写、校对、格式、引用、双语或结构化写作，且目标是产出可编辑学术产物时触发。
- **不使用**：纯文献综述检索、无材料凭空写作、仅问答、与学术写作无关的通用任务。
- **相对基础 Agent 的增益**：材料索引、事实-引用-章节三向对齐、结构化中间表示、缺项检测、一致性待核清单、改动留痕、诚实降级。

## 2. 输入契约

- 运行时读取 `/input/case.json`（任务说明、`output_contract`、材料清单与约束）与 `/input/task.md`（题目与运行约定）。
- 材料按题面提供：PDF 用 `pdftotext`/`pdf-render-tool` 真实解析，图片表格用 `ocr-tool`，办公文档用 `office-renderer`；
  **不依据文件名或领域标签补造数据**，解析失败标 `[无法解析]`。
- 中间产物写 `/app/work/`；最终可编辑产物与 `result.json` 写 `/app/output/`。
- 本 Skill 脚本只用 Python 标准库，在 Skill 根目录（`/skills/weekly-skill`）下以**相对路径**调用，路径经命令行传入：
  `python3 scripts/main.py init|check|finalize --input /input --output /app/output --work /app/work`。

## 3. 输出契约与中间表示

必须产出 `/app/output/result.json`，顶层键：

- `case_id`：来自 `/input/case.json` 的 id/case_id，缺省用文件名；
- output_contract **全部字段**：以 case.json 的 output_contract 为准；未定义时默认 `deliverable`、`fact_source_map`、`version_limits`（含义见 references/conventions.md）；
- `status`：`in_progress | partial | complete | failed | insufficient_evidence | input_error`，必须与实际完成度一致；
- `sources`：每条事实/引用的来源（材料位置 + 文献条目）；`uncertainty`：无法确认、冲突、缺失项；
- `pending_human`：需人工确认项（每项可带 `resolved: true` 表示已闭环）；`errors`/`degradations`：异常与降级记录；
- `change_log`：每次改动的 ts/step/note；`runtime`：start/end/elapsed。

主稿件写 `/app/output/manuscript.md`（保留层级、图注编号、引用格式）。要求 docx/xlsx/pptx 时另渲染真实 PDF/PNG 预览。

**中间表示（/app/work/，脚本自动生成骨架，Agent 逐材料填充）**：

| 文件 | 内容 | 作用 |
|-|-|-|
| `contract_meta.json` | 解析出的 output_contract、材料清单、任务提示 | 契约的机器可读副本 |
| `plan.json` | 章节计划、图/表计划、引用计划、开放问题 | 写作前的可复核蓝图 |
| `material_index.json` | 每材料 key_claims/numbers/citations/flags/conflicts | 事实库：每条主张带 locator + 逐字 quote |
| `figure_model.json` | 每图/表的图注、面板、数值-单位绑定、来源 | 图表与正文三向对齐 |
| `evidence_map.json` | `[S:]`/`[C:]` 与材料索引的交叉核对结果 | 悬空来源/悬空引用检测 |
| `consistency_report.json` | 章节完整、图/表编号、status 诚实性检查 | 收口前的自查报告 |

## 4. 执行流程（预算 900 秒 / 64 次请求 / 单次 ≤16384 token）

**硬规则：前 3 分钟内必须先落盘 result.json 骨架与全部中间表示，之后只做增量改进；任何阶段不得编造数据。**

- **Step 0 读取与骨架**（0–3 分钟，约 4–8 次请求）
  读 `/input/case.json`、`task.md`；执行
  `python3 scripts/main.py init --input /input --output /app/output --work /app/work`。
  产出 `result.json`（status=in_progress/insufficient_evidence/input_error）+ 六个中间表示骨架。此后任何中途失败都仍有合法 partial 结果。
- **Step 1 材料索引与证据抽取**（3–15 分钟，约 10–20 次请求）
  逐材料读取，把关键断言、数值、单位、样本量、图注、文献条目写回 `material_index.json` 的 key_claims/numbers/citations/flags（每条含 locator + 逐字 quote）；图/表同步写 `figure_model.json`；把必要章节写进 `plan.json`。冲突与缺失记入 flags/conflicts。
- **Step 2 撰写/改写稿件**（15–30 分钟，约 15–25 次请求）
  按 output_contract 与学科模板写 `/app/output/manuscript.md`；每个事实/数字/引用挂 `[S:材料ID.定位]` 或 `[C:键]`；缺项标 `[缺失]`/`[待确认]`/`[冲突待确认]`，不硬填。
- **Step 3 一致性复核**（30–40 分钟，约 8–12 次请求）
  执行 `python3 scripts/main.py check --output /app/output --work /app/work`；
  按 `evidence_map.json`/`consistency_report.json` 修正悬空来源、悬空引用、缺章节、图/表编号冲突；引用区用 `[C:键] 作者. 题目. 期刊 年份;卷(期):页. DOI` 逐条定义。
- **Step 4 收口**（40–50 分钟，约 5–8 次请求）
  汇总 `sources`/`uncertainty`/`pending_human`/`change_log`，把未决项标记 `resolved` 或保留为待人工确认；执行
  `python3 scripts/main.py finalize --output /app/output --work /app/work --status complete|partial`，
  渲染预览；留 5–10 分钟余量处理异常。

（时间与请求数为预算示意；核心原则是"先落盘、再增量、终复核"。）

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] `python3 scripts/selftest.py` 全部通过（结构、空输入、坏 JSON、缺输入、schema）
- [ ] result.json 含 case_id + output_contract 全字段 + status/sources/uncertainty/pending_human/change_log
- [ ] 稿件是完整可编辑章节稿，不是提纲、写作建议或素材堆叠
- [ ] 每个数值/单位/样本量/图注/作者/年份/期刊/DOI 可在 material_index 或文献条目回溯
- [ ] 每条 `[C:键]` 在参考文献区定义且支撑所在句；无装饰性、无错配引用
- [ ] 关键章节、图表、参考文献、声明字段齐备，缺项已显式标注
- [ ] 摘要与结果、正文与图注、方法与步骤无冲突；待核清单已写入 consistency_report
- [ ] 改动留痕（change_log 记录每次修改）
- [ ] 未确认/冲突/缺软件/缺数据已如实写入 uncertainty/pending_human 与 limitations，无编造
- [ ] status 诚实：有未决项时不得标 complete

完整清单（含逐项处置动作）见 references/checklist.md；字段 schema 见 references/conventions.md。

## 6. 降级策略

| 情况 | 处置 |
|-|-|
| 空输入目录 | 退出码 0，status=`insufficient_evidence`，不抛异常 |
| 坏 JSON / 坏编码 / 文件超 2MB | 记录到 `errors`/`degradations`，继续处理其余材料，不崩溃 |
| PDF/OCR/办公文档解析失败 | 用可用工具；失败则把该材料标 `[无法解析]`，不凭文件名补写 |
| 图表缺图注 | 图注标 `[缺失]`，正文相应描述标 `[待确认]`，计入 uncertainty |
| 文献取不到全文 | 仅用给定元数据引用；缺失字段写 `unknown`，禁止补 DOI |
| 材料间数值/口径冲突 | 并列各方 + 来源，标 `[冲突待确认]`；取舍时显式声明依据 |
| 材料超长 / 格式不认识 | 按可用部分产出 `partial`，说明未处理部分及原因 |
| 脚本运行失败 | 保留已落盘骨架，手工补字段，如实记录失败原因 |

**通用规则：缺什么标什么，绝不编造。** 无法产出的部分写 `partial` 并在 `uncertainty`/`degradations`/`change_log` 说明 limitations。
