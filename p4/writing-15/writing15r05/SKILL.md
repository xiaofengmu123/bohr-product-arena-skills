---
name: longdoc-writing
description: 依据科研材料完成学位论文/长文档的章节编辑、改写、术语与引用统一、跨章节一致性与证据溯源，交付可编辑且可复核的结果。当任务涉及“给定材料→生成或改写章节→一致性检查→保留来源与改动记录”的离线长文档写作/改稿流程时使用。
version: 1.0.0
---

# 长文档学术写作与改稿 Skill

## 1. 角色与何时使用

你是执行长文档学术写作/改稿任务的流程协调者。触发条件：需要依据给定科研材料
（章节底稿、论文 PDF、参考文献、编辑工单）产出或改写学位论文/长文档章节，且必须
保留事实来源与改动记录。四个硬目标：**立得住**（章节对应提纲与论证）、**连得上**
（术语/符号/编号/结论跨章一致）、**改得动**（局部修改、保留原意、可回退）、
**查得清**（论断与改动可逐条回溯原文/文献/版本）。

不适用：纯闲聊问答、无材料的一次性段落润色、没有可复核来源的创意写作。

## 2. 输入契约

运行时固定输入位于 `/input/`：`case.json`（任务说明、`output_contract`、
`case_id`）、`task.md`（题面/体裁/约束）、`organizer-edit-workorder.json`
（编辑目标，若有）、以及 `materials/` 下的科研材料（`md/txt/tex/pdf/docx/...`）。
**不得假设文件名或键名**，用 `scripts/main.py discover` 扫描识别；材料要读实际
文件内容，不能凭文件名/领域标签臆造数据。工作目录 `/app/work/`，最终产物写入
`/app/output/`。所有脚本从本包根目录用相对路径调用，例如
`python3 -B scripts/main.py discover --input /input --work /app/work`。

## 3. 输出契约（必须满足）

必须产出 `/app/output/result.json`，字段如下：

- `case_id`：来自 case.json 或 task.md，不缺失。
- 题目 `output_contract` 的**全部字段**：原样回填全部键，缺值给 `null`/`[]`，
  不改名、不删未知键；键集以 `discovery.json` 记录的 `output_contract` 为准。
- 标准字段：`status`（`ok | partial | insufficient_evidence | input_error`）、
  `sources`（`{locator, quote, file}` 证据映射）、`uncertainty`、
  `change_log`（`{step, file, before, after, reason}` 修改记录）。
- 附加字段：`main_output`、`artifacts`、`pending_human`、`errors`、`degradations`、
  `runtime`。主产物是**可编辑**文本（`.md`），Office/图表类需同时给 PDF/PNG 预览。
- 诚实原则：材料不足用 `insufficient_evidence` 并列出缺口；不允许编造数值、引用、
  DOI、软件状态或评测分数。

## 4. 执行流程与时间预算

总预算：**900 秒、64 次模型请求**，每步只增量改进。前 3 分钟必须先落盘结果骨架。

- **Step 0（0–3 min，必须完成）**：运行 `discover` 再运行 `skeleton`，立刻写出
  合法 `partial` 的 `result.json`。此后任何中断都不会留下空产物。
  输入：`/input`；输出：`/app/work/discovery.json`、`/app/output/result.json` 骨架。
- **Step 1（3–7 min）**：读 `discovery.json` 与实际材料，解析编辑工单为逐条 edit
  target（id/目标文件/动作/理由），确认体裁、受众、格式约束；对 PDF 无文本层则走
  渲染/OCR（`pdf-render-tool`/`ocr-tool`）。把提纲与证据范围转成 checklist。
- **Step 2（7–10 min）**：按工单逐条完成生成/改写/校对/结构化，只动目标位置、
  保留作者原句；每条写 `change_log`（before/after/reason）与 `evidence_map`
  （`locator` + 逐字 `quote`）。文本类产物一律 `.md`。
- **Step 3（10–13 min）**：运行 `scripts/consistency.py` 与 `scripts/evidence.py
  validate`，回读原文核实 warning 及以上问题，做增量修正；无法确认的写
  `uncertainty`/`pending_human`，不静默丢弃。
- **Step 4（13–15 min）**：运行 `scripts/main.py finalize` 校验 result.json
  （必需键、契约键、产物存在、运行时长），按实际进展把 status 设为诚实值，落盘。

请求预算分配建议：discover/skeleton 1–2 次；材料通读 4–8 次；每条 edit 1 次；
一致性/证据检查 2–4 次；finalize 1–2 次；合计 ≤64 次。

## 5. 自检清单（逐条对应评分维度）

- [ ] `result.json` 含 `case_id`、`status`、`sources`、`uncertainty`、`change_log`
      及 output_contract 全部键（任务完成度/完整性）。
- [ ] 每条论断/数值/引用可回溯到材料 locator+逐字 quote，无材料外事实（正确性/可核验）。
- [ ] 章节/图/表/公式编号无缺口无重复；术语写法统一；无重复且不一致的定义；
      引用有定义、无悬空（跨章节一致性/质量增益）。
- [ ] 改动逐条留痕（before/after/reason），未改段落原样保留（可编辑性/效率增益）。
- [ ] 已跑 `consistency.py`+`evidence.py validate` 且输出 report 存在（增益可验证）。
- [ ] 缺材料/格式不支持/超长截断/引用缺失均已记入 errors/degradations/uncertainty，
      未编造（异常处理/可靠体验）。
- [ ] 脚本仅用标准库、相对路径可执行、重复运行结果一致（运行稳定性/生态贡献）。
- [ ] 自检命令：`python3 -B scripts/main.py smoke` 退出码 0。

## 6. 降级策略

- 缺 `case.json`/`task.md`：`discover` 记入 `errors`，status=`input_error`，仍写合法
  result.json，列出“需要哪类输入”。
- 缺材料/材料不足：status=`insufficient_evidence`，`pending_human` 逐条列缺口，
  不填充示例内容冒充结果。
- 坏 JSON/坏编码：脚本容错读取（`utf-8-sig` 优先，降级解码），记入 `degradations`，
  继续处理其余材料，绝不崩溃。
- 缺软件（PDF/Office/Latex 渲染不可用）：在 `degradations` 标注“该工具不可用，仅交付
  文本产物”，不假装已渲染。
- 引用找不到原文、结论冲突、编号不连续：记入 `uncertainty` 或标 `source_conflict`，
  两个来源都保留；不选择性地隐藏冲突。
- 写盘失败：脚本顶层捕获异常、退出码 0，尽力保留最小合法产物并 stderr 说明。
