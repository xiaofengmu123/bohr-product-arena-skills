---
name: paper-structure-review
description: 论文结构与逻辑优化 Skill。对一篇完整稿件做六维结构诊断，并把每条判断落到原文 locator 与逐字 quote，交付作者可直接执行的编辑动作（移动/合并/拆分/收窄/补充限制），最终写入 /app/output/result.json。适用于研究问题、章节结构、论证链、段落关系、重复内容、结论边界六类结构问题的定位、改稿方案与复核；不用于凭空改写整篇稿件或补造材料外事实。
version: 1.0.0
---

# 论文结构与逻辑优化

## 1. 角色与何时使用

本 Skill 在任务要求"对给定稿件完成结构诊断并给出可执行修改方案"时启用：读入论文/底稿与材料，逐条找出结构问题，说明它属于哪一类，并给出"移到哪、与哪段合并、从哪拆分、哪句需收窄"的编辑动作，而不是另起一篇改写稿。

适用：研究问题与贡献对齐、章节结构与叙事顺序、论证链与论据对应、段落关系与重复、结论边界六类诊断；改稿、校对、双语或结构化写作中需要保留事实与来源的任务。

不适用：需要新实验或新数据填补核心空白的任务；仅润色语言而不涉及结构判断的任务。材料不足时如实报告缺口，不补造内容。

本 Skill 的突破口是"证据溯源"：每条结论都带 `locator`（文件+章节/段落/句子定位）与逐字 `quote`，并映射到六维问题分类。模型负责判断与写作，脚本负责发现材料、落盘骨架、抽取候选锚点与校验契约。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（案例元数据、任务说明、材料清单、可能的 `output_contract`）与 `/input/task.md`。输入目录不假定固定文件名，需实际扫描。
- 材料文件：`/input/` 下的 PDF、图片、表格、Markdown/LaTeX/文本、参考文献等。PDF 可能没有预解析层，必须读取实际内容，不能凭文件名或领域标签猜测。
- 工作目录 `/app/work`（中间产物）；最终产物目录 `/app/output/`。
- 脚本位于本 Skill 根目录的 `scripts/`，统一用相对路径 `scripts/bootstrap.py` 调用；以 Skill 根目录为工作目录。若从 `/app/work` 启动，先 `cd /skills/weekly-skill`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，合法 JSON），结构如下：

- `case_id`：取自 case.json 的 `case_id` 或 `id`，原样保留。
- 题目 `output_contract` 的全部字段：从 case.json 的 `output_contract`（或任务说明）读取键名，逐字段填写，不改名、不删未知键。若未显式给出，按默认契约输出：`diagnosis`（六维结构诊断）、`edit_plan`（逐条可执行编辑动作）、`evidence_map`（事实与来源映射）、`version_boundary`（修改差异与人工确认项）、`artifacts`（可编辑文件与预览路径）。
- `status`：`complete | partial | insufficient_evidence | input_error` 之一，诚实反映完成度。
- `sources`：数组，逐条 `{claim_id, claim, source_ref, confidence}`；`source_ref` 写"文件+章节/段落/图表编号"。
- `uncertainty`：数组，记录材料不足、冲突、未确认项及原因。
- `pending_human`：数组，逐条写需要作者裁决的项（含问题、依据、选项）。
- `change_log`：数组，按时间顺序 `{time, action, status}`，至少含骨架落盘与每次增量修改。
- `runtime`：`{started_at, finished_at, model_requests, seconds}`（如实记录，禁止伪造分数）。

`evidence_map` 每条结构（强化版）：`{claim_id, dimension, claim, locator, quote, basis, edit, confidence, status, verdict}`。`locator` 写"文件 + 段落/行号 + 章节"；`quote` 必须为原文逐字片段（可省略中缀用 `…`）；`basis` 说明判断依据；`edit` 写编辑动作与目标位置；`verdict` 为 `confirmed | needs_human | rejected`。

`result.json` 允许多次增量覆盖，每次写入必须 JSON 合法、字段完整。可编辑产物（Markdown/JSON）写到 `/app/output/`；docx/pptx/图类产物另存 PDF/PNG 预览。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

硬性要求：开始后 3 分钟内必须先落盘一版 `result.json` 骨架（`status=in_progress`，各字段空值/占位），之后只做增量改进；任何失败都不允许不落盘。

- 步骤 0（0–2 分钟）：读 `/input/case.json` 与 `/input/task.md`，确认 case_id、体裁、受众、格式、output_contract。运行 `python scripts/bootstrap.py` 生成骨架 result.json；输入 case.json/task.md，输出 `/app/output/result.json` 骨架。change_log 记第一条。
- 步骤 1（2–5 分钟）：盘点 /input 材料。运行 `python scripts/evidence.py` 生成 `/app/work/evidence.json`，按文本/表格/PDF/图片分类，抽取章节标题、候选数值/术语/论断与上下文锚点，标记无法直接读取的文件（图片需 render/OCR）。输入 /input，输出 `/app/work/evidence.json` 与材料清单。
- 步骤 2（5–10 分钟）：六维诊断与证据核对。逐条定位问题到章节/段落/句子，写 `claim`、`quote`、`basis`、`edit`、`verdict`；冲突信息两者都记录并标 `source_conflict`。增量写回 result.json 的 evidence_map 与 sources。
- 步骤 3（10–13 分钟）：产出六维 diagnosis 与可执行 edit_plan，写 version_boundary（修改差异 + pending_human）。每个诊断/动作均指向对应 claim_id，不新增材料外事实。
- 步骤 4（13–15 分钟）：自检与定稿。运行 `python scripts/verify.py` 校验字段完整、evidence_map 每条有 locator 与逐字 quote、结论可回溯、局限如实标注；修复 FAIL/WARN；status 置 complete/partial/insufficient_evidence，填 uncertainty/change_log/runtime。输出 `/app/output/result.json` 终稿 + `/app/output/verify_report.json`。

剩余时间用于增量改进：优先补 evidence_map 的 quote 与 pending_human，不推翻已落盘内容。某步缺材料立即降级（见第 6 节），不空转。

## 5. 自检清单（逐条勾选，对应评分维度）

实际效果（40 分）：

- [ ] 已对一篇完整稿件完成结构诊断，并交付作者可直接执行的修改方案（任务完成度）。
- [ ] 结构判断与稿件实际内容相符：不虚构不存在缺陷、不漏真实断裂、不把风格偏好当结构错误（正确性）。
- [ ] 覆盖研究问题、章节结构、论证链、段落关系、重复内容、结论边界六类，且缺项已说明非稿件主要问题（完整性）。
- [ ] 每条判断与修改建议都能指回原文位置与逐字依据，可独立复核（可核验性）。

使用增益（25 分）：

- [ ] 起草前已用脚本盘点材料与候选锚点，而非凭记忆（质量/效率）。
- [ ] 骨架先行、增量改进，返工与修改记录在 change_log（增益可验证）。

创新增益（15 分）：

- [ ] 每条结论带 locator + 逐字 quote，evidence_map 结构细粒度可回溯（新增能力）。
- [ ] 六维问题分类与证据强度分级规则统一，可复用（方法改进）。

可靠体验（10 分）：

- [ ] 触发条件明确、脚本可运行、路径有效、重复运行一致（易用/稳定）。
- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/稿件截断已识别并降级，未编造（异常处理）。

生态贡献（10 分）：

- [ ] 说明、依赖（仅 Python 标准库）、配置、范围、限制齐全（文档完整）。
- [ ] 分类法、检查清单、evidence_map 模板与脚本可被他人复用（可复用性）。

## 6. 降级策略

- 缺文件/缺软件（无 OCR、无 LaTeX、无渲染器）：在 uncertainty 与 limitations 中如实标注"未提供/不可用"，相应字段留空并说明，status 置 partial；绝不伪造读数、软件状态或科学数据。
- 稿件被截断、编码损坏、图表/公式缺失：不猜测内容，标为待确认（pending_human），正文用"[待确认：xxx]"占位或改用稳健表述。
- 引用找不到或文献不全：只写已有文献支持的论断，缺失部分标注"无文献支持"，不虚构 DOI/引用。
- 运行失败或超时：保留已落盘的 partial result.json（含 status、sources、uncertainty、pending_human、change_log），说明失败原因。

## 7. 脚本调用（仅 Python 标准库）

- `python scripts/bootstrap.py`：生成骨架 result.json（含默认契约字段）。
- `python scripts/evidence.py`：盘点材料、抽取章节/候选锚点 → `/app/work/evidence.json`。
- `python scripts/verify.py`：校验 result.json 契约与可回溯性 → `/app/output/verify_report.json`。
- `python -B scripts/main.py --case /input/case.json --task /input/task.md --input /input --work /app/work --out /app/output`：一键 bootstrap→evidence→verify。
- 环境变量可覆盖默认路径：`CASE_JSON`、`TASK_MD`、`INPUT_DIR`、`WORK_DIR`、`OUTPUT_DIR`、`RESULT_JSON`。

最小调用示例（示意，非正式案例）：`python -B scripts/main.py --input ./demo/input --work ./demo/work --out ./demo/output`；示意输出须标注为示例。禁止用脚本伪造评测分数或运行结果。
