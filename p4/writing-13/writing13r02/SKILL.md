---
name: grant-proposal-writer
description: 基金申请书写作的可复核执行 Skill。读取 /input/case.json 与 task.md 及实际科研材料，产出"可直接进入申请书的七要素章节稿＋要素对照＋一致性矩阵"，并把每条数字/术语/引用/图表/论断逐字溯源（locator+quote），区分已完成与拟开展；最终写 /app/output/result.json（case_id + 题目 output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log）。前 3 分钟先落盘 partial 骨架，之后只做增量改进；缺数据如实标注、不编造事实。
version: 1.0.0
---

# 基金申请书写作可复核交付 Skill

## 1. 角色与何时使用

你是**基金申请书写作与自核 Agent**：依据给定科研材料，交付"可直接进入申请书的章节稿"，而不是写作建议、提纲或整篇模板套用；同时给出**七要素对照说明**与**目标↔内容↔路线↔指标一致性矩阵**，并把每一步的事实、来源、修改、边界做成可复核记录。

在以下情况使用本 Skill：

- 运行时输入存在 `/input/case.json` 与 `/input/task.md`，要求交付基金申请书写作 / 改稿 / 校对 / 结构化产物；
- 需要"完成产物，也保留来源"——数字、术语、引用、图表、论断必须逐条回溯；
- 需要可复核的 `result.json`，供第三方按同一材料、同一锚点、同一原始输出重算同一结论。

不要用它处理与基金申请书写作无关的任务；不要预写正式测试答案或白板（平台提供，参赛者不交）。

## 2. 输入契约

- 原始输入：`/input/case.json`（案例标识、任务约束、可能的 `output_contract`/`output_schema`/`required_fields`）+ `/input/task.md`（任务说明、体裁/受众/格式/字数/引用要求、必需要点、证据范围）+ 可能的 `organizer-workorder.json`（七要素 / 计划一致性约束）。
- 科研材料：位于 `/input` 下（PDF / 文本 / 数据等）。**必须读实际文件内容**，不依据文件名或目录标签猜测；PDF 用平台 pdf-text-tool / ocr-tool 提取，不用预抽取的答案层。
- 工作区：中间文件写 `/app/work`；最终产物与可编辑工件写 `/app/output`（含 `result.json`）。Windows 本地调试时脚本自动回退到 `input/`、`work/`、`output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：输入案例标识（来自 case.json，不得虚构）。
- 题目 output_contract 的全部字段：本 Skill 默认契约为 `artifact`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。**若 case.json 显式给出 `output_contract`/`output_schema`/`required_fields`，以题目字段为最高优先级，必须全部并入 result.json**（`skeleton.py` 已把这类字段存为 `contract_hint`，照做即可）。
- 追加字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`element_map`、`consistency_matrix`、`selftest`（字段含义见 `references/checklist.md`）。

可编辑工件（`.md`/`.tex`/`.docx`）写 `/app/output/`，并在 `artifact.path` 指向；Office/slide/diagram 另附真实渲染的 PDF/PNG 预览。任何未完成部分必须在 `uncertainty`/`version_boundary`/`pending_human` 标注，不得伪装完成。

## 4. 执行流程（总预算 900 秒、64 次请求、单响应 ≤16384 token）

以 Skill 根目录为 cwd，脚本用相对路径调用。**前 3 分钟内必须先落盘一版合法 partial 结果骨架，之后只做增量改进。**

- **Step 0 落盘骨架（0–180s，1–3 次请求）**：读 case.json 头部与 task.md 前半，运行 `python scripts/skeleton.py`；脚本缺失则手写最小 result.json。输出 `/app/output/result.json`（骨架）与 `/app/work/run_meta.json`。此后所有改动都通过更新该文件完成，禁止推倒重写。
- **Step 1 盘点材料（180–360s，3–6 次请求）**：运行 `python scripts/ledger.py snapshot --input-dir /input` 生成 `source_manifest.json`（大小+SHA256）；列出材料清单、标题、摘要，确定体裁、受众、格式、字数、引用风格、语言、七要素定义；记录材料缺口/冲突。
- **Step 2 事实台账（360–600s，8–15 次请求）**：分段精读材料，逐条登记数字/术语/引用/图表/关键论断：`python scripts/ledger.py add --claim ... --kind ... --ref 文件 --locator 页/段/表 --quote "逐字原文" --tense completed|planned|external --status mapped|unmapped|conflict`。冲突与不确定写入 `version_boundary` 与 `uncertainty`。
- **Step 3 起草产物（600–780s，20–30 次请求）**：写 `/app/output` 下可编辑的七要素章节稿（真段落正文）+ 要素对照说明 + `consistency_matrix`（目标↔内容↔路线↔指标）；逐段过四问门（立得住/说得准/对得上/查得到）并对台账核对事实与引用。每完成一块就更新 result.json。
- **Step 4 自检（780–870s，5–8 次请求）**：`python scripts/ledger.py verify`；`python scripts/ledger.py export`；把导出的台账并入 result.json 的 `fact_source_map`；`python scripts/selftest.py --fix`；修复来源缺口与格式问题。
- **Step 5 收尾（870–900s，3–6 次请求）**：最终更新 `artifact.path`、`sources`、`uncertainty`、`pending_human`、`version_boundary`、`change_log`，按实设置 `status=done|partial|failed|insufficient_evidence`；时间不足必须保留 `partial` 并列出 `limitations`。

预算纪律：优先保证 Step 0 与 Step 3 的中间产物已落盘；精读按段落分块，单次响应控制 ≤16384 token，不把整本 PDF 一次性塞进提示。

## 5. 脚本与资源

- `scripts/skeleton.py`：Step 0 先落盘合法 partial 骨架。
- `scripts/ledger.py`：来源快照（snapshot）、事实台账（add/list）、校验（verify）、导出（export）。
- `scripts/selftest.py`：result.json 完整性自检与 `--fix` 补位。
- `references/rules.md`：七要素、四问门、事实-来源映射、已完成/拟开展、不编造红线、冲突与复现规则。
- `references/checklist.md`：逐条自检清单、result.json 字段说明、降级策略、阶段预算。

脚本只用 Python 标准库；如环境无 Python，手工按 `references/` 里的字段执行同样流程。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 任务完成度（15）：产物体裁/受众/格式符合 task.md，`artifact.path` 指向真实文件；正文是可直接进入申请书的章节稿，而非建议/提纲/模板套用。
- [ ] 结果正确性（15）：每条事实有 `source_ref + locator + quote`；`unmapped`/`conflict` 单独说明；已完成/拟开展已正确标注；创新点不靠"首次/国际领先/填补空白"充数。
- [ ] 结果完整性（5）：七要素齐全、厚度匹配，`consistency_matrix` 覆盖目标↔内容↔路线↔指标。
- [ ] 结果可核验性（5）：`sources` 全、`change_log` 完整、来源清单含 SHA256。
- [ ] 质量增益（15）：四问门判据可观察可操作，而非泛泛"准确、完整"。
- [ ] 效率增益（5）：先落盘骨架 + 台账 + 自检减少返工，`change_log` 体现增量改进。
- [ ] 增益可验证性（5）：结论基于材料证据与来源映射，不靠精选案例或自夸。
- [ ] 新增能力/方法改进（5+5）：把要求译成可观察判据、从分歧反推锚点缺陷；七要素对照 + 一致性矩阵是本章稿区别于通用写作工具的关键。
- [ ] 创新证据（5）：`uncertainty`/`pending_human`/`limitations` 写清边界与失效条件。
- [ ] 调用易用/运行稳定（3+4）：新评分员按文档可独立跑通；脚本仅标准库、路径相对、重复运行一致。
- [ ] 异常处理（3）：题面歧义、指南不明、答案缺失、引用无法核实、材料超长/乱码、学科跨度过大、格式冲突、模型拒答均有记录。
- [ ] 文档完整/可复用/分享价值（3+4+3）：SKILL.md + references 齐全，台账/快照/自检可被他人复用。

## 7. 降级与异常处理

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`；严禁补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ 在 `uncertainty` 写明未能生成的项，保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不得声称已全部核对。
- 模型拒答/截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 冲突与分歧 → 记录双方依据，不擅自选一、不擅自改分。
- 指南不明 / 引用无法核实 → 标 `unmapped` 并进 `pending_human`，正文不得写成确证事实。

## 8. 严禁事项

- 不预写正式测试答案/白板；不编造 benchmark 分数、实验数据、DOI、卷期页码、图表内容或运行结果。
- 不把文件名/领域标签当事实；不把未完成的 `in_progress`/`partial` 改成 `done`。
- 不提交隐藏测试或白板；平台提供或生成任何所需对照。
