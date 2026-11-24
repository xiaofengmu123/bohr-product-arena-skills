---
name: paper-structure-review
description: 科研论文结构与逻辑诊断 Skill。对整篇稿件做六维结构诊断（研究问题对齐、章节叙事、论证链、段落关系、重复内容、结论边界），输出可执行编辑动作（移动/合并/拆分/收窄/补过渡）与逐条可回溯的论点—论据映射；结构化中间产物 plan.json、text_model.json、claim_model.json、redundancy.json、chapter_model.json 可复核可复现；最终写入 /app/output/result.json。适用：论文结构与逻辑优化、章节重构、论证链检查、结论边界收窄、双语或结构化改写、校对与引用整理。
version: 1.0.0
---

# 论文结构与逻辑优化

## 1. 角色与何时使用

本 Skill 在任务要求对一篇完整稿件做"结构与逻辑"诊断/优化时启用：依据给定科研材料产出结构诊断与作者可直接执行的修改方案，并保留来源可回溯。核心目标是四个"得"：**诊得准**（定位到章节/段落/句子并说出问题类型）、**改得动**（给出移动/合并/拆分/收窄等可执行动作，而非另写一篇）、**链得通**（研究问题—方法—结果—结论闭环，每条论断有对应论据）、**说得清**（每条判断指回原文位置与依据，作者可采纳/部分采纳/驳回）。

不适用：需要新实验、新数据或新文献检索来填补核心空白的任务——应如实报告材料缺口，不补造内容。本 Skill 的突破口是"可复核的结构诊断"：先用脚本产生稳定 locator 与候选锚点，模型只在锚点上做语义判断。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（案例元数据、任务说明、材料清单、可能的 output_contract）与 `/input/task.md`。
- 材料文件：`/input/` 下提供的稿件（Markdown/LaTeX/文本/CSV/统计输出）、图、PDF。PDF 可能没有预解析文本层，必须实际读取或调用 pdf-text-tool/pdf-render-tool/ocr-tool，不能凭文件名或领域标签猜测。
- 工作目录：`/app/work`（中间产物）；最终产物目录：`/app/output/`。
- 脚本位于本 Skill 根目录 `scripts/`，以相对路径调用；若 Harness 从 `/app/work` 启动，先 `cd /skills/weekly-skill`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 合法 JSON），字段：

- `case_id`：取自 case.json 的 `case_id` 或 `id`，原样保留。
- 题目 output_contract 的全部字段：从 case.json 的 `output_contract` 读取键名逐字段填写；若未显式给出，按默认契约输出：
  - `diagnosis`：目标产物，六维结构诊断（可编辑 Markdown，含 findings 与 not_a_problem）。
  - `editing_plan`：改得动，可执行编辑动作数组，每条含 `action_type / target_locator / rationale / evidence_locators / status`。
  - `claim_map`：链得通，论点—论据映射，每条含 `claim_text / locator / quote / evidence_locators / chain_status`。
  - `source_map`：说得清，事实与来源映射，每条含 `claim_id / locator / quote / confidence`。
  - `version_boundary`：版本与边界，含 `summary / diffs / material_gaps / pending_human`。
- `status`：`complete` / `partial` / `failed`（材料不足时也可 `insufficient_evidence`，取诚实值）。
- `uncertainty`：材料不足、冲突、无法判断项及原因。
- `pending_human`：需人工确认项。
- `change_log`：按时间顺序 {time, action, status}，至少含骨架落盘与每次增量修改。
- `errors` / `degradations`：坏 JSON、坏编码、跳过的文件等运行异常。

可编辑产物（`diagnosis.md`、`editing_plan.md`、`source_map.json`、`diff.md`）写到 `/app/output/`；纯文本任务无需渲染预览，Office/图类产物才需另存 PDF/PNG 预览。`result.json` 允许多次增量覆盖，每次写入保持 JSON 合法、字段完整。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**硬性要求：开始后 3 分钟内必须落盘一版合法 result.json 骨架，之后只做增量改进；任何失败都不允许不落盘。**

- 步骤 0（0–1 分钟）：读 `/input/case.json` 与 `/input/task.md`，确认 case_id、体裁、受众、格式、output_contract。运行 `python scripts/main.py skeleton` 生成骨架（status=in_progress，各字段空值/[]，change_log 记第一条）。输入：case.json/task.md；输出：result.json 骨架。
- 步骤 1（1–4 分钟）：盘点与分段。运行 `python scripts/main.py analyze` 生成中间产物：`/app/work/inventory.json`（材料清单）、`text_model.json`（稳定 locator 分段）、`claim_model.json`（论断/结论/因果/过度措辞候选锚点）、`redundancy.json`（近重复句对）、`chapter_model.json`（章节篇幅）、`plan.json`（预算与步骤状态）。
- 步骤 2（4–8 分钟）：六维诊断。按 `references/structure-taxonomy.md` 的 D1–D6 逐条定位，每条 finding 写 locator+quote；无问题的维度写 not_a_problem 并给一句定位证据。逐条回写 result.json 的 diagnosis 与 source_map。
- 步骤 3（8–11 分钟）：可执行修改方案。按动作表产出 `editing_plan`（move/merge/split/narrow/insert_transition/add_evidence/move_detail），每条给 rationale 与 evidence_locators；同步写 `claim_map` 并标 chain_status（supported/weak/broken/circular/unverifiable）。
- 步骤 4（11–13 分钟）：结论边界收窄。claim_model 中 causal/overclaim 标记句逐条比对数据范围，相关性写因果则产出 narrow 动作并记 diff；补限制说明或标注缺失。
- 步骤 5（13–15 分钟）：自检定稿。运行 `python scripts/main.py verify` 校验契约与可回溯性，修复 FAIL/WARN；填 uncertainty/pending_human/change_log，status 置诚实值，可编辑产物落盘。

剩余时间用于增量改进：优先补来源映射、论据链与局限，不推翻已落盘内容。某步缺材料立即降级（第 7 节），不空转。

## 5. 六维诊断与中间表示（本版侧重点：可复核）

六维：D1 研究问题与贡献主张对齐；D2 章节结构与叙事顺序；D3 论证链与论据对应；D4 段落关系与重复内容；D5 结论边界与表述尺度；D6 其他结构缺陷（缺章节/缺参考文献/图表公式缺失/截断等）。

**中间表示是可复核性的根基**：脚本先产出稳定 locator（`文件#P段落.S句子`、`文件#H标题`）与候选锚点，模型只核对、不做无依据判断。每条结论必须能指回 `text_model.json` 的 locator 与逐字 quote；重复内容用 `redundancy.json` 的 jaccard 佐证；篇幅失衡用 `chapter_model.json` 的 chars 占比佐证；结论边界用 `claim_model.json` 的 causal/overclaim 标记佐证。这些中间产物留在 `/app/work/`，使判断过程可被独立复核、可复现。

## 6. 自检清单（逐条勾选，对应评分维度）

实际效果（40 分）：
- [ ] diagnosis 与 editing_plan 均已产出，为可编辑、作者可直接对照执行的文本（任务完成度）。
- [ ] 每条 finding 都有 locator+quote，不虚构缺陷、不漏真实断裂、不把风格偏好当结构错误（正确性）。
- [ ] 六维均有结论（含 not_a_problem 定位证据），缺项不是稿件主要问题所在（完整性）。
- [ ] source_map/claim_map 每条可回溯到原文位置（可核验性）。

使用增益（25 分）：
- [ ] 起草前已运行 analyze 产生候选锚点与中间表示，而非凭记忆（质量/效率）。
- [ ] 骨架先行、增量改进，返工次数记录于 change_log（增益可验证）。

创新增益（15 分）：
- [ ] 论点—论据映射、跨章节冗余检测、结论与数据范围比对由脚本+模型协同完成（新增能力）。
- [ ] 六维问题分类与可执行动作表统一于 references/structure-taxonomy.md（方法改进）。

可靠体验（10 分）：
- [ ] 触发条件明确、脚本可运行、路径有效、重复运行结果一致（易用/稳定）。
- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/稿件截断已标注并降级，未编造（异常处理）。

生态贡献（10 分）：
- [ ] 说明、依赖（仅 Python 标准库）、配置、范围、限制齐全（文档完整）。
- [ ] 分类表、检查清单、中间产物 schema 与脚本可被他人复用（可复用性）。

## 7. 降级策略

- 缺文件/缺软件（无 pdf-text-tool、无 OCR、无渲染器）：在 result.json 的 uncertainty 与 version_boundary.material_gaps 中如实标注"未提供/不可用"，相应字段留空并说明，status 置 partial；绝不伪造读数、软件状态或科学数据。
- 图表/PDF 读数不清、单位不明、因果方向无法确定：不猜测，标为待确认（pending_human），正文用"[待确认：xxx]"占位或改用稳健表述。
- 引用找不到或文献不全：只写已有文献支持的论断，缺失部分标"无文献支持"。
- 运行失败或超时：保留已落盘 partial result.json（含 status、source_map、uncertainty、change_log），说明失败原因。

## 8. 脚本调用（仅 Python 标准库）

- `python scripts/main.py skeleton`：生成 result.json 骨架（空输入时置 insufficient_evidence，退出码 0）。
- `python scripts/main.py analyze`：材料盘点 + 分段 + 论点/冗余抽取 → `/app/work/*.json`。
- `python scripts/main.py verify`：校验 result.json 契约与可回溯性 → `/app/output/verify_report.json`。
- `python scripts/selftest.py`：在临时目录跑正常/空输入/坏 JSON 三场景自检，输出 PASS/FAIL。

环境变量可覆盖默认路径：CASE_JSON、TASK_MD、INPUT_DIR、WORK_DIR、OUTPUT_DIR、RESULT_JSON。禁止用脚本或其他方式伪造评测分数或运行结果。
