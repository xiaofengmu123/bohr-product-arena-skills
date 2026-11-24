---
name: academic-paper-writing
description: >
  在给定科研材料（事实底稿、实验记录、图表、参考文献）基础上完成学术论文章节的生成、改写、校对、
  引用与格式任务，保证数值、单位、样本量、图注与来源一致，每条引用可回溯、缺项显式标注，
  产出可继续编辑的稿件与可复核的 result.json。适用于学术写作的生成、改写、校对、格式、引用、
  双语与结构化写作任务。执行时先落盘 partial 骨架、再增量改进、终做一致性复核。
version: 1.0.0
---

# 学术论文写作 Skill（fact-traceable academic writing）

## 1. 角色与何时使用

本 Skill 把 Agent 约束为「事实可追溯的学术写作引擎」：只从 `/input` 给定材料取事实，逐条标注
`[S:来源]` 与 `[C:引用]`，产出可编辑、可逐条复核的稿件与 result.json。

- **何时使用**：任务属于学术写作的生成、改写、校对、格式、引用、双语或结构化写作，且目标为
  可编辑学术产物时触发。
- **不使用**：纯文献检索、无材料凭空写作、仅问答、与学术写作无关的通用任务。
- **相对基础 Agent 的增益**：材料索引、证据映射表、事实-引用-章节三向对齐、缺项检测、改动留痕、
  早落盘（异常时也不会留下空产物）。

## 2. 输入契约

- 运行时读取 `/input/case.json`（任务说明、output_contract、材料清单与约束）与 `/input/task.md`
  （题目与评分/运行约定）；科研材料按题面路径读取，不依据文件名或领域标签补造数据。
- PDF 用 `pdftotext`/渲染工具真实解析，图片/表格用 OCR/渲染工具；解析失败即标注，不虚构内容。
- 中间产物写 `/app/work/`；最终可编辑产物与 `result.json` 写 `/app/output/`。
- 脚本只依赖 Python 标准库；在 Skill 根目录用相对路径调用，例如
  `python3 scripts/init_result.py --case /input/case.json --out /app/output/result.json --work /app/work`。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层键：

- `case_id`：取自 case.json 的 id/case_id，缺省用文件名；
- output_contract **全部字段**：以 case.json 的 output_contract 为准，**原样回填全部键、不改名、
  不删除未知键**；未定义时默认 `deliverable`、`fact_source_map`、`version_limits`
  （含义见 references/conventions.md）；
- `status`：`in_progress | partial | complete | failed`，必须与实际完成度一致；
- `sources`：每条事实/引用的来源（材料位置 + 文献条目）；
- `uncertainty`：无法确认、冲突、缺失、需人工确认项；
- `pending_human`：需要人最终裁决的清单（与 uncertainty 互补，给到具体待办项）；
- `change_log`：每次改动的 `ts/step/note`，从骨架到成稿全程留痕。

主稿件作为可编辑 Markdown 写 `/app/output/manuscript.md`（保留章节层级、图注编号、引用格式）；
要求 docx/xlsx/pptx 时另渲染真实 PDF/PNG 预览。事实性断言用 `[S:材料ID或locator]` 标来源、
`[C:键]` 标引用，文末参考文献区以 `[C:键] 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义条目。
详细 schema 见 references/conventions.md。

## 4. 执行流程（硬预算：900 秒 / 64 次请求 / 单次 ≤16384 token）

**硬规则：前 120 秒内必须先落盘一版 `result.json`（status=partial），之后只做增量改进；任何阶段
不得编造数据。**

| 阶段 | 累计时间窗 | 请求预算 | 输入 | 输出 | 关键动作 |
|-|-|-|-|-|-|
| P0 落盘骨架 | 0–120 s | ≤6 | case.json, task.md | result.json(partial), work/contract_meta.json | 立即调用 init_result.py |
| P1 材料索引 | 120–420 s | ≤14 | 全部材料 | work/material_index.json | 逐材料抽取断言/数值/单位/图注/文献，记录冲突缺失 |
| P2 撰写成稿 | 420–660 s | ≤20 | index + 契约 | output/manuscript.md | 按契约与模板写完整章节稿，缺项标 `[缺失]`/`[待确认]` |
| P3 一致性复核 | 660–800 s | ≤12 | 稿件 + index | work/evidence_map.json | 跑 evidence_map.py 并修正悬空来源/引用 |
| P4 收口 | 800–870 s | ≤8 | 全部产物 | result.json(final), 预览 | 填 status/sources/uncertainty/change_log，跑 selftest.py |
| 余量兜底 | 870–900 s | ≤4 | — | — | 异常处置、终审、失败降级 |

要点：900 秒即 15 分钟，P0 的落盘不是「写完再存」，而是第一步先写 partial 骨架；后续每步只改
result.json 中对应字段并追加 change_log。若到 P3 时间不够，直接以 partial 收口，绝不虚标 complete。

## 5. 自检清单（逐条对应评分维度）

- [ ] result.json 存在且为合法 JSON；含 case_id + output_contract 全字段 + status/sources/uncertainty/pending_human/change_log
- [ ] status 与实际完成度一致（缺项用 partial，不得虚标 complete）；change_log 非空
- [ ] 稿件是完整可编辑章节稿，不是提纲、写作建议或素材堆叠
- [ ] 每个数值/单位/样本量/图注/作者/年份/期刊/DOI 可回溯到 material_index 的具体 locator + 逐字 quote
- [ ] 每条 `[C:键]` 在文末定义且支撑所在句，无装饰性、无错配引用；DOI/卷期缺失写 unknown，不杜撰
- [ ] 关键章节、图表、参考文献、声明字段齐备，缺项已显式标注
- [ ] 摘要与结果、正文与图注、方法与步骤无冲突；待核清单已写入 uncertainty/pending_human
- [ ] 运行 evidence_map.py：无悬空来源、无悬空引用、无「定义未用」的可疑条目
- [ ] 运行 selftest.py：退出码 0
- [ ] 材料缺失/冲突/解析失败已如实写入 uncertainty 与 limitations，无编造

完整清单（含逐项处置动作与降级表）见 references/checklist.md。

## 6. 降级策略（缺什么标什么，绝不编造）

- 缺 PDF 解析/OCR：用可用工具；失败则把该材料标 `[无法解析]`，不凭文件名或标签补写。
- 文献取不到全文：仅用给定元数据引用，缺失字段写 `unknown`，禁止杜撰 DOI。
- 材料冲突：并列各方 + 来源，标 `[冲突待确认]`；需取舍时显式声明依据。
- 缺图注/材料超长/格式不认识：按可用部分产出 partial 结果，status=partial，写入 limitations。
- 脚本运行失败：保留已落盘骨架，手工补字段，并如实记录失败原因与 degradations。

## 7. 脚本说明（均只依赖标准库，从 Skill 根目录用相对路径调用）

- `scripts/init_result.py`：P0 早落盘，写合法 partial result.json 骨架（容忍坏 JSON/坏编码/缺文件）。
- `scripts/evidence_map.py`：P3 交叉核验 `[S:]`/`[C:]`，输出 work/evidence_map.json 与报告。
- `scripts/selftest.py`：P4 交付前自检，校验输出契约与 SKILL.md frontmatter，退出码 0/1。
