---
name: paper-structure-review
description: 科研论文"结构与逻辑"诊断 Skill。对一篇完整稿件做六维结构诊断——研究问题与贡献对齐、章节结构与叙事顺序、论证链与论据对应、段落关系与重复内容、结论边界、表述尺度——把每个问题定位到具体章节/段落/句子（locator）并附逐字原文（quote），给出作者可直接执行的编辑动作（移动/合并/拆分/收窄/补过渡/换论据），并把全部判断写入 /app/output/result.json，含 case_id、题目 output_contract 全部字段、sources、uncertainty、pending_human、change_log。适用于论文结构诊断、改稿方案、论证闭环检查、跨章节冗余检测与结论边界比对；不做纯语言润色，不补造缺失材料。
version: 1.0.0
---

# 论文结构与逻辑诊断

## 1. 角色与何时使用

本 Skill 在任务要求"对一篇论文做结构/逻辑诊断并给出可执行修改方案"时启用。输入通常是一份完整稿件（Markdown/LaTeX/txt/PDF 提取文本）、任务说明与格式/受众约束；输出不是"另起一篇改写稿"，而是定位到具体位置的问题清单 + 逐条编辑动作 + 论证链映射，让作者能逐条复核"采纳 / 部分采纳 / 驳回"。

聚焦四个目标：
- **诊得准**：把问题定位到 章节→段落→句子，并说明属于哪一类结构问题，不写放之四海而皆准的写作原则。
- **改得动**：给出可执行编辑动作（move/merge/split/narrow/reorder/insert_transition/reassign_evidence），而非另写全文。
- **链得通**：研究问题→方法→结果→结论形成闭环，每条论断有对应论据，跨章节呼应成立。
- **说得清**：每条判断都有 locator + 逐字 quote，作者可独立复核。

不适用：纯语言润色、需要新实验/新数据才能回答的核心空白、需要新增文献检索填补结论的情形——这些只如实报告缺口，不补造。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（案例元数据、任务说明、材料清单、可能的 `output_contract`）与 `/input/task.md`。
- 材料文件：`/input/` 下的稿件与附件。PDF 只提供真实 PDF、无预解析答案层，必须用 `pdftotext`/渲染/OCR 实际读取，不得凭文件名或领域标签猜测。
- 工作目录：`/app/work`（中间产物）；最终产物：`/app/output/`（含 result.json）。
- 脚本位于 Skill 根目录 `scripts/`，用相对路径 `scripts/main.py` 调用。若 Harness 从 `/app/work` 启动，先 `cd /skills/weekly-skill` 再调用。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON）。顶层字段：

- `case_id`：取自 case.json 的 `case_id` 或 `id`，原样保留。
- 题目 `output_contract` 的全部字段：逐键回填，缺值给 `null`/`[]`，不改名、不删未知键。若 case.json 未给 `output_contract`，按本默认契约输出：
  - `diagnosis`（主产物）：六维结构诊断 Markdown 全文。
  - `edit_plan`：逐条可执行编辑动作 `[{id, issue_id, action, from, to, instruction, reason, quote}]`。
  - `source_map`：事实与来源映射 `[{claim_id, claim, locator, quote, source_ref, confidence}]`。
  - `version_boundary`：`{diff_summary, material_gaps, anomalies}` 版本与边界。
  - `artifacts`：可编辑产物路径数组（如 `/app/output/diagnosis.md`、`/app/output/source_map.json`）。
- `status`：`complete | partial | insufficient_evidence | input_error | failed` 之一，必须诚实。
- `sources`：证据台账，每条 `{claim_id, locator, quote, source_ref, confidence}`；`locator` 为"文件+章节/段落+行号"，`quote` 为材料逐字原文。
- `uncertainty`：材料不足、冲突、未确认项及原因与影响。
- `pending_human`：需人工确认项 `{item, why, suggested_resolution}`。
- `change_log`：按时间 `{time, action, status, note}`，至少含骨架落盘与每次增量修改。

`result.json` 允许多次增量覆盖，每次写入必须保持 JSON 合法、字段完整。可编辑产物（`.md`/`.json`/`.csv`）写到 `/app/output/`；Office/图类产物另存 PDF/PNG 预览。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

硬性要求：**前 3 分钟内必须落盘一版 result.json 骨架，之后只做增量改进**；任何失败都不得让 result.json 缺席。

- 步骤 0（0–1 分钟）：读 `/input/case.json` 与 `/input/task.md`，确认 case_id、任务类型、受众、格式、output_contract。运行 `python scripts/main.py` 落盘骨架 result.json 与 /app/work/evidence.json。输入：case.json/task.md/材料；输出：result.json 骨架（status=partial，change_log 第一条）。
- 步骤 1（1–6 分钟）：盘点与切分材料。用 evidence.json 里的段落切分与候选论断（带 locator+quote）定位正文；对 PDF/图片用 pdftotext/渲染/OCR 读入，无法读取的记录到 uncertainty。
- 步骤 2（6–12 分钟）：六维诊断。逐维扫描（见 references/structure-taxonomy.md）：研究问题对齐、章节叙事顺序、论证链—论据对应、段落关系与重复、结论边界、表述尺度。每条问题写 diagnosis + 对应 edit_plan 动作，并把关键论断写回 sources/source_map（locator+quote）。
- 步骤 3（12–14 分钟）：复核与降噪。删除把风格偏好当结构错误的条目，确认每处缺项不是稿件的主要问题；填 version_boundary、pending_human、uncertainty。
- 步骤 4（14–15 分钟）：自检与定稿。运行 `python scripts/verify.py` 校验契约、可回溯性与状态；修复 FAIL/WARN；status 置终值；更新 change_log。

剩余时间用于增量改进：优先补 sources 的 locator/quote 与 pending_human，不推翻已落盘内容。预算内某步缺材料，立即降级（第 6 节）。

## 5. 自检清单（逐条勾选，对应评分维度）

实际效果（40 分）：
- [ ] diagnosis 与 edit_plan 均已产出，为可编辑、作者可逐条执行的诊断（任务完成度）。
- [ ] 每条判断与稿件实际内容一致，无虚构缺陷、无漏报真实断裂、不把风格偏好当结构错误（正确性）。
- [ ] 六维（研究问题/章节结构/论证链/段落关系/重复内容/结论边界）均覆盖，且缺项非稿件主要问题（完整性）。
- [ ] 每条关键判断有 locator + 逐字 quote，可独立复核（可核验性）。

使用增益（25 分）：
- [ ] 已用 evidence.json 的段落切分与候选论断，而非凭记忆（质量/效率）。
- [ ] 骨架先行、增量改进，返工与轮次记录于 change_log（增益可验证）。

创新增益（15 分）：
- [ ] 论证链以"论断—论据映射表"形式给出，而非泛泛评语（新增能力）。
- [ ] 结论边界用"相关性/因果动词 + 数据范围"比对表检查（方法改进）。

可靠体验（10 分）：
- [ ] 触发条件明确、脚本可用、相对路径有效、重复运行结果一致（易用/稳定）。
- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/稿件截断均已标注并降级，未编造（异常处理）。

生态贡献（10 分）：
- [ ] 说明、依赖（仅 Python 标准库）、配置、范围、限制齐全（文档完整）。
- [ ] 六维分类、标注模板、检查清单与脚本可复用（可复用性）。

## 6. 降级策略

- 缺文件/缺软件（无 pdftotext、无 OCR、无渲染器）：在 uncertainty 与 version_boundary 中如实标注"未提供/不可用"，相应字段留空说明，status 置 partial/insufficient_evidence；绝不伪造读数、软件状态或科学数据。
- 图表/公式读不清、单位不明：不猜测，标为 pending_human，正文用"[待确认：xxx]"占位或改用稳健表述。
- 某论断无对应论据、引用找不到：只写已有材料支持的判断，缺失处标注"无材料支持/无参考文献"。
- 稿件被截断：仅诊断已见部分，并在 version_boundary.anomalies 记录"截断于 locator"。
- 运行失败/超时：保留已落盘的 partial result.json（含 status、sources、uncertainty、change_log），说明失败原因；禁止伪造评测分数或运行结果。

## 7. 脚本调用（仅 Python 标准库）

- `python scripts/main.py`：骨架先行 + 材料盘点 + 契约自检，一次性跑通并保证 result.json 已落盘。
- `python scripts/evidence.py`：材料发现、文本解码、段落切分、候选论断抽取 → /app/work/evidence.json。
- `python scripts/verify.py`：校验 result.json 契约与可回溯性 → /app/output/verify_report.json。
- `python scripts/selftest.py`：空输入/坏 JSON/最小稿件 + 完整样本契约自检，退出码 0 为通过。

环境变量可覆盖默认路径：`CASE_JSON`、`TASK_MD`、`INPUT_DIR`、`WORK_DIR`、`OUTPUT_DIR`、`RESULT_JSON`、`EVIDENCE_JSON`、`VERIFY_JSON`。禁止以任何方式伪造评测分数或运行结果。
