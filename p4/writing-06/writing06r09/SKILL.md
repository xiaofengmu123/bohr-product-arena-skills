---
name: paper-structure-review
description: 论文"结构与逻辑优化"Skill。对一篇完整稿件做六维结构诊断（研究问题、章节结构、论证链、段落关系、重复内容、结论边界），每条判断定位到章节/段落/句子并给出可执行编辑动作与逐字依据，交付可复核的结构诊断报告与修改方案，最终写入 /app/output/result.json。适用于依据给定稿件的结构优化、论证链校验、冗余识别与结论边界检查任务。
version: 1.0.0
---

# 论文结构与逻辑优化

## 1. 角色与何时使用

本 Skill 在任务要求"对一篇完整稿件做结构诊断并交付作者可直接执行的修改方案"时启用。输入通常含稿件全文（Markdown/LaTeX/PDF/文本）、任务说明、受众与格式约束。产出是六维结构诊断 + 逐条可执行编辑动作 + 证据溯源，不是语言润色或整篇重写。

不适用：纯语言润色、语法校对、需要新数据/新文献补核心内容的写作任务——此时如实报告材料缺口。

本 Skill 的突破口是"论点—论据映射 + 可执行编辑动作"，对应题目四问：
- 诊得准：诊断落到章节/段落/句子并分类，而非泛泛写作原则。
- 改得动：每条判断给出动作（移动到哪、与哪合并、从哪拆分、哪句收窄）。
- 链得通：校验研究问题—方法—结果—结论闭环，论断有对应论据。
- 说得清：每条判断指回原文位置与依据，作者能决定采纳/部分采纳/驳回。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（案例元数据、任务说明、材料清单、可能的 output_contract）与 `/input/task.md`。
- 材料文件：`/input/` 下的稿件全文及相关文件；PDF 必须实际读取内容（pdftotext），不能凭文件名或领域标签猜测。
- 工作目录 `/app/work`；最终产物 `/app/output/`。
- 脚本位于 Skill 根目录 `scripts/`，用相对路径调用；若 Harness 从 `/app/work` 启动，先 `cd /skills/weekly-skill`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON），含：

- `case_id`：取自 case.json 的 `case_id` 或 `id`，原样保留。
- 题目 output_contract 全部字段：从 case.json 的 `output_contract`（或 `outputContract`）读取键名并逐字段填写，缺值给 `null`/`[]`；若未给出，默认契约四个字段——`diagnosis`（六维结构诊断）、`edit_plan`（可执行修改方案）、`source_map`（事实与来源映射）、`version_boundary`（修改差异与人工确认项）。
- `status`：`complete`/`partial`/`insufficient_evidence`/`input_error`/`failed`。
- `sources`：数组，逐条 `{claim_id, claim, source_ref, quote, confidence}`；`quote` 为原文逐字片段。
- `uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`limitations`、`runtime`。

可编辑产物（诊断报告 `.md`、修改方案 `.json`/`.csv`）写到 `/app/output/`；Office/幻灯片/图类产物另存 PDF/PNG 预览。result.json 允许多次增量覆盖，每次必须 JSON 合法、字段完整。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

硬性要求：开始后 3 分钟内必须落盘一版 `partial` result.json 骨架，之后只做增量改进；任何失败都不允许不落盘。

- 步骤 0（0–1 分钟）：读 `/input/case.json` 与 `/input/task.md`，确认 case_id、体裁、受众、格式、output_contract。运行 `python scripts/main.py build` 生成骨架（status=partial、字段空值、change_log 记第一条）。
- 步骤 1（1–4 分钟）：盘点材料。运行 `python scripts/main.py discover` 列文件与类型；`python scripts/structure.py <稿件文件>` 输出分段、候选论断、候选冗余对与定位锚点 → `/app/work/structure.json`。
- 步骤 2（4–8 分钟）：六维诊断。逐条核对候选锚点，按 references/rules.md 六维分类给出发现：定位 + 问题类型 + 严重度 + 逐字依据。每条发现写入 `diagnosis`，并同步 `sources`（claim_id→source_ref→quote）。用 `python scripts/main.py patch` 增量写回。
- 步骤 3（8–12 分钟）：改得动。把每条发现转成可执行编辑动作（move/merge/split/narrow/insert-transition/reword-scale/delete/reorder），写入 `edit_plan`，与 `diagnosis` 一一对应。
- 步骤 4（12–14 分钟）：链得通 + 结论边界。校验研究问题—方法—结果—结论闭环，逐条论断给论据映射；检查结论是否超出数据范围、相关性是否写成因果，写入 `source_map` 与 `version_boundary`。
- 步骤 5（14–15 分钟）：自检定稿。运行 `python scripts/main.py validate` 校验契约与可回溯性；运行 `python scripts/selftest.py` 包级自检；修复 FAIL/WARN；status 置为最终值，填 uncertainty/pending_human/change_log/limitations。

剩余时间增量改进：优先补来源映射、局限与人工确认项，不推翻已落盘内容。某步缺材料立即降级（第 9 节），不空转。

## 5. 六维诊断方法（详见 references/rules.md）

六维：`research_question`、`chapter_structure`、`argument_chain`、`paragraph_relation`、`redundancy`、`conclusion_boundary`。每个发现字段：`id`、`dimension`、`locator`（章节+段落+句子）、`type`（问题类型）、`severity`（critical/major/minor）、`quote`（逐字）、`edit`（可执行动作）、`basis`（判定依据）、`decision`（accept/partial/reject）。诊断只针对稿件真实内容，不虚构缺陷，不把风格偏好当结构错误。

## 6. 编辑动作动词表

每条发现必须落到具体动作，动词与含义：

- `move`：把某句/某段移动到指定位置（移动到哪）。
- `merge`：与指定段落/章节合并（与哪合并）。
- `split`：从指定位置拆出新段/新节（从哪拆分）。
- `narrow`：收窄论断范围（样本/条件/强度）。
- `insert-transition`：在指定位置补过渡句。
- `insert-evidence`：在指定位置补证据/文献/数据。
- `reword-scale`：改写措辞尺度（因果→相关、绝对→有条件）。
- `delete`：删除重复或无关内容。
- `reorder`：调整章节/段落顺序。

动作必须写明目标位置（如 `§3.2 para[2] 前`）与具体做法，避免"建议补充过渡"这类无落点的空动作。

## 7. 复核决策支持

作者复核每条发现时应能独立判断，因此每个发现给出 `decision`：
- `accept`：依据清晰、动作可执行，可直接采纳。
- `partial`：判断成立但动作需按上下文调整，标注可调部分。
- `reject`：经复核可能不成立，标注依据的薄弱点。

有冲突（同一指标两处来源不一致）时，两处都记录并标 `source_conflict`，写入 uncertainty，不擅自取舍。

## 8. 自检清单（逐条勾选，对应评分维度）

实际效果（40 分）：

- [ ] 六维均被覆盖，缺项仅为稿件非主要问题（完整性）
- [ ] 每个发现定位到章节/段落/句子且与原文相符，无虚构缺陷（正确性）
- [ ] 修改方案是可执行动作而非整篇重写（可操作性）
- [ ] 每个发现/结论有 locator + 逐字 quote（可核验性）

使用增益（25 分）：

- [ ] 骨架先行、增量改进，change_log 记录每次改动（增益可验证）
- [ ] 脚本产出候选锚点，模型只核对不凭空生成（质量/效率）

创新增益（15 分）：

- [ ] 提供论点—论据映射与跨章节冗余检测（新增能力）
- [ ] 六维分类与结论边界比对规则统一（方法改进）

可靠体验（10 分）：

- [ ] 触发条件明确、脚本可运行、路径有效（易用/稳定）
- [ ] 缺章节/缺参考文献/图表公式缺失/截断已标注并降级（异常处理）

生态贡献（10 分）：

- [ ] 说明、依赖（仅 Python 标准库）、配置、范围、限制齐全（文档完整）
- [ ] 规则、检查清单、脚本可复用（可复用性）

## 9. 降级策略

- 缺材料/缺软件（无 OCR、无 LaTeX、无渲染器）：在 limitations/uncertainty 中如实标注"未提供/不可用"，相应字段留空并说明，status=partial；绝不伪造读数或科学数据。
- 图表/公式缺失或无法定位：标为 pending_human，正文用"[待确认：xxx]"占位，不猜测。
- 稿件被截断：在 limitations 记录截断位置，仅诊断可见部分，不推断缺失内容。
- 无参考文献/图表：相应维度标注"无材料可查"，不虚构引用、DOI、数值。
- 运行失败或超时：保留已落盘的 partial result.json（含 status、sources、uncertainty、change_log），说明失败原因。

## 10. 脚本调用（仅 Python 标准库）

- `python scripts/main.py build`：生成 partial 骨架 result.json。
- `python scripts/main.py discover`：盘点 /input 材料清单。
- `python scripts/structure.py <file>`：分段/候选论断/冗余检测/定位 → `/app/work/structure.json`。
- `python scripts/main.py patch <file>`：把补丁 JSON 对象合并进 result.json（安全增量更新）。
- `python scripts/main.py validate`：校验 result.json 契约与可回溯性 → verify_report.json。
- `python scripts/selftest.py`：包级自检（空目录、坏 JSON、缺输入、示例文本）。

环境变量可覆盖默认路径：CASE_JSON、TASK_MD、INPUT_DIR、WORK_DIR、OUTPUT_DIR、RESULT_JSON。禁止伪造评测分数或运行结果。

## 11. 最小调用示例（示意，非正式案例结果）

```text
输入：/input/case.json（case_id=example-1）、/input/task.md、/input/manuscript.md
命令：cd /skills/weekly-skill && python scripts/main.py build
      python scripts/structure.py /input/manuscript.md
诊断产物：/app/output/result.json（diagnosis 含 F01 等发现）、/app/output/diagnosis.md
```

## 12. 边界

本 Skill 只做结构级诊断与可执行修改方案，不替代语言润色、翻译或数据生成；与润色类工具的增量在于"论点—论据映射、跨章节冗余检测、结论—数据范围比对"。稿件无结构问题时应如实给出"未见明显结构缺陷"，不强行凑问题。
