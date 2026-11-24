---
name: fact-traceable-academic-writing
description: >
  在给定科研材料（事实底稿、实验记录、图表、参考文献）基础上完成学术论文的
  生成、改写、校对、格式、引用、双语与结构化写作。以证据目录（evidence catalog）
  为单一事实来源，强制数值/单位/样本量/图注/引用逐条可回溯（locator + 逐字
  quote），先落盘 partial 结果再增量改进，产出可编辑 Markdown 稿件与可复核的
  result.json。适用于写作-01 学术论文写作赛道及同类科研写作任务。
version: 2.0.0
---

# 事实可追溯的学术论文写作（fact-traceable academic writing）

## 1. 角色与何时使用

本 Skill 把通用模型约束为「事实可追溯的学术写作引擎」：只在给定科研材料内取事实，
逐条标注来源（locator + 逐字 quote）与引用，产出可编辑、可逐条复核的章节稿。

- **使用**：`/input/case.json` 的任务属于学术写作的生成、改写、校对、格式、引用、
  双语或结构化写作，且目标是可编辑学术产物。
- **不使用**：纯文献检索、无材料凭空写作、仅问答、与学术写作无关的通用任务。
- **相对基础 Agent 的增益**：材料清单、证据目录（单一 IR）、事实-引用-章节三向对齐、
  缺项检测、一致性待核清单、改动留痕、降级自检。

## 2. 输入契约

- 运行时读取 `/input/case.json`（任务、`output_contract`、材料清单、约束）与
  `/input/task.md`（题目与评分/运行约定）。
- 材料按题面提供；PDF 用 pdftotext/pdftoppm 真实解析，图片/表格用 OCR/渲染，
  不依据文件名或领域标签补造数据。
- 中间产物写 `/app/work/`；最终可编辑产物与 `result.json` 写 `/app/output/`。
- 脚本只用 Python 标准库，在 Skill 根目录（`/skills/weekly-skill`）下用相对路径调用。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层键：

- `case_id`：来自 `/input/case.json` 的 id/case_id/challenge_id/task_id，缺省文件名；
- output_contract **全部字段**：以 case.json 的 `output_contract` 为准；未定义时默认
  `deliverable`、`fact_source_map`、`version_limits`（含义见 references/conventions.md）；
- `status`：`in_progress | partial | complete | insufficient_evidence | input_error | failed`；
- `sources`：每条事实/引用的来源（材料位置 + 文献条目）；
- `uncertainty`：无法确认、冲突、缺失、需人工确认项；
- `pending_human`：必须由作者确认的事项；
- `change_log`：每次改动的 `ts`/`step`/`note`。

主稿件写 `/app/output/manuscript.md`（保留层级、图注编号、引用定义行）；要求
docx/xlsx/pptx 时另渲染真实 PDF/PNG 预览。事实断言用 `[S:材料ID]` 标来源、`[C:键]`
标引用，文末用 `[C:键]: …` 定义文献条目。详细 schema 见 references/conventions.md。

## 4. 执行流程（预算 900 秒 / 64 次模型请求 / 单次 ≤16384 token）

**硬规则：前 3 分钟内必须先落盘 result.json 骨架与材料清单，之后只做增量改进；
任何阶段不得编造数据。**

- **Step 0 骨架与发现（0–3 分钟，约 4–8 次请求）**
  读 `/input/case.json`、`task.md`；执行
  `python3 scripts/bootstrap.py --input-dir /input --output-dir /app/output --work-dir /app/work`
  产出 `/app/work/material_manifest.json`、`/app/work/evidence_catalog.json`、
  `/app/output/result.json`（status=in_progress）及契约占位产物。
- **Step 1 证据抽取入目录（3–18 分钟，约 10–20 次请求）**
  逐材料读取，把关键断言、数值、单位、样本量、图注、文献条目用
  `python3 scripts/evidence.py add-claim …` 与 `add-citation …` 写入证据目录；
  每条断言附 locator + 逐字 quote；冲突与缺失写 `conflicts`/`gaps`。
- **Step 2 撰写/改写稿件（18–32 分钟，约 15–25 次请求）**
  按 output_contract 与学科模板写 `/app/output/manuscript.md`；每个事实/数字/引用
  挂 `[S:]`/`[C:]`；缺项标 `[缺失]`/`[待确认]`，不硬填。
- **Step 3 一致性复核（32–42 分钟，约 8–12 次请求）**
  先 `python3 scripts/evidence.py check --catalog … --draft …`，再
  `python3 scripts/selftest.py --check …`，按报告修正悬空来源/引用、缺项、图号冲突。
- **Step 4 收口（42–50 分钟，约 5–8 次请求）**
  渲染 `/app/output/fact_source_map.json` 与 `/app/output/version_limits.json`，填
  `status`/`sources`/`uncertainty`/`pending_human`/`change_log`；留 5–10 分钟余量。

（时间与请求数为预算；核心原则是「先落盘、再增量、终复核」。）

## 5. 自检清单（逐条对应评分维度）

- [ ] result.json 含 case_id + output_contract 全字段 + status/sources/uncertainty/pending_human/change_log
- [ ] 稿件是完整可编辑章节稿，不是提纲、写作建议或素材堆叠
- [ ] 每个数值/单位/样本量/图注/作者/年份/期刊/DOI 可在证据目录逐字回溯
- [ ] 每条 `[C:键]` 在文末定义且支撑所在句；无装饰性、无错配引用
- [ ] 关键章节、图表、参考文献、声明字段齐备，缺项已显式标注
- [ ] 摘要与结果、正文与图注、方法与步骤无冲突；待核清单已输出
- [ ] 改动留痕（change_log 记录每次修改）
- [ ] 未确认/冲突/缺软件/缺数据已如实写入 uncertainty 与 limitations，无编造
- [ ] `python3 scripts/selftest.py --selftest` 退出码 0（提交前必跑）

完整清单（含逐项处置动作）见 references/checklist.md。

## 6. 降级策略

- 缺 PDF 解析/OCR：用可用工具；失败标 `[无法解析]`，不凭文件名或标签补写。
- 文献取不到全文：仅用给定元数据引用，缺失字段写 `null`/`unknown`，不杜撰 DOI。
- 材料冲突：并列各方 + 来源，标 `[冲突待确认]`；取舍时显式声明依据。
- 缺图注/材料超长/格式不认识：按可用部分产出 `partial`，status=partial，写入 limitations。
- 脚本运行失败：保留已落盘骨架，手工补字段，如实记录失败原因。

## 7. 脚本调用与入口

| 脚本 | 作用 | 退出码 |
|-|-|-|
| `scripts/bootstrap.py` | 输入发现 + 材料清单 + partial 骨架 | 恒为 0 |
| `scripts/evidence.py` | 证据目录记录 + 证据映射校验 | check 有 error 时 1 |
| `scripts/selftest.py` | 一致性复核 / 提交前自检 | 未通过时 1 |

路径用相对路径；缺省 `--input-dir /input`、`--output-dir /app/output`、
`--work-dir /app/work`。本包无单体 main.py，改由三个单一职责脚本按 Step 0→3 顺序
调用；证据目录是唯一中间表示，脚本只读写它，避免多份索引漂移。

## 8. 边界

- 不做通用大而全的写作系统：一个 Skill 只解决「材料 → 可追溯章节稿」一类任务。
- 无真实材料时输出 `insufficient_evidence`，不用示例填充。
- 交付前在独立目录跑一次 `selftest.py --selftest`，验证空输入/坏 JSON/缺输入
  三种降级场景均退出码 0 且产物合法。
