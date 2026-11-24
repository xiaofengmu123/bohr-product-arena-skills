---
name: fund-proposal-writing-pipeline
description: 基金申请书写作的可复核执行 Skill。围绕"立得住、说得准、对得上、查得到"四道闸门，用三层中间表示（来源清单→证据台账→要素映射）组织七要素章节稿，先校验后成稿，逐条保留 locator+逐字 quote 来源，并在 /app/output/result.json 交付 case_id、status、artifact、fact_source_map、version_boundary、sources、uncertainty、pending_human、change_log 等可复核字段。适用于基金申请书立项依据/科学问题/研究目标/研究内容/创新点/技术路线/研究基础与可行性等章节写作、改稿、格式、引用与结构化写作任务。前 3 分钟先落盘 partial 骨架，之后只做增量改进，缺数据时如实标注不编造。
version: 1.0.0
---

# 基金申请书写作：三层证据链流水线

## 1. 角色与何时使用

你是**基金申请书写作与自核 Agent**：依据给定科研材料完成指定的章节稿写作/改稿/格式/引用/双语/结构化产物，把每一步事实、来源、修改、边界做成可复核记录。

在以下情况使用本 Skill：
- 运行时存在 `/input/case.json` 与 `/input/task.md`，任务要求交付基金申请书写作类产物；
- 需要“完成产物，也保留来源”——数字、术语、引用、图表、论断必须逐条回溯；
- 需要第三方按同一材料、同一证据映射重算同一结论。

不要用于与申请书写作/评测无关的任务；不要预写正式测试答案或白板（平台提供，参赛者不交）。

## 2. 输入契约

- 原始输入：`/input/case.json`（案例标识、任务约束、可能的 `output_contract`/`required_fields`）+ `/input/task.md`（体裁/受众/格式/字数/引用/七要素要求、证据范围）。
- 科研材料：位于 `/input` 下（PDF/文本/数据等）。**必须读实际文件内容**，不依据文件名或领域标签猜测；PDF 用平台 pdf-text-tool/ocr-tool 提取，不用预抽取答案层。
- 工作区：中间文件写 `/app/work`；最终产物与可编辑工件写 `/app/output`（含 `result.json`）。本地调试时脚本自动回退到 `input/`、`work/`、`output/` 相对目录。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：
- `case_id`：输入案例标识；
- 题目 `output_contract` 的全部字段：默认契约为 `artifact`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。**若 case.json 显式给出 `output_contract`/`output_schema`/`required_fields`，以题目字段为最高优先级，原样并入 result.json**；
- 追加字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`（字段含义见 `references/checklist.md`）。

可编辑工件写 `/app/output/` 并在 `artifact.path` 指向；Office/slide/diagram 另附真实渲染的 PDF/PNG 预览（`artifact.preview_path`）。任何未完成部分必须在 `uncertainty`/`version_boundary` 标注，不得伪装完成。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

以 Skill 根目录为 cwd（必要时 `cd /skills/weekly-skill`），脚本用相对路径 `scripts/evidence.py`。**前 3 分钟内必须先落盘 partial 结果骨架，之后只做增量改进，禁止推倒重写。**

- **Step 0 骨架（0–180s，1–2 次请求）**：读 case.json 与 task.md 前半，运行 `python3 -B scripts/main.py bootstrap --case /input/case.json`；再运行 `python3 -B scripts/main.py inventory` 生成来源清单（SHA256）。产出：`/app/output/result.json`（partial 骨架）+ `/app/work/workbench.json`。此后所有改动都通过更新这两个文件完成。
- **Step 1 盘点材料（180–360s，3–6 次请求）**：列出材料清单、标题、摘要，确定任务类型（写作 vs 评测构建）、体裁、受众、格式、字数、引用风格、语言、七要素要求；记录材料缺口/冲突。
- **Step 2 证据抽取（360–600s，8–15 次请求）**：分段精读材料，逐条登记证据——每条必须含 `locator` + 逐字 `quote`。先把 patch 写成 JSON 文件（如 `/app/work/patch.json`，含 `evidence_bank`/`element_map`/`consistency_matrix` 键），再运行 `python3 -B scripts/main.py record --json-file /app/work/patch.json`；也可用 `--json '<...>'` 内联（跨平台优先用 `--json-file`）。冲突与不确定写入 `version_boundary` 与 `uncertainty`。
- **Step 3 要素映射与一致性（600–720s，12–18 次请求）**：把证据归入七要素 `element_map`，填 `consistency_matrix`（目标/内容/路线/指标四列逐行对齐）；运行 `python3 -B scripts/main.py check` 跑四道闸门，先校验后成稿。check 在存在缺口时返回退出码 1，这是闸门信号而非崩溃，按报告继续补齐缺口。
- **Step 4 起草产物（720–840s，15–20 次请求）**：写 `/app/output` 下可编辑章节稿 + 要素对照说明（标注可接受变体与实质错误）；逐段对照证据台账核对，每完成一块就更新 result.json。
- **Step 5 自检收尾（840–900s，5–8 次请求）**：`python3 -B scripts/main.py check`（退出码 1 表示仍有缺口，属正常闸门信号）；`python3 -B scripts/selftest.py`；`python3 -B scripts/main.py finalize --status done|partial`；按实设置 status。

预算纪律：优先保证 Step 0 与 Step 4 的中间产物已落盘；精读按段落分块，单次响应 ≤16384 token，不把整本 PDF 一次性塞进提示。

## 5. 脚本与资源

- `scripts/main.py`：编排入口 `bootstrap|inventory|record|check|finalize`（薄入口，<30KB）。
- `scripts/evidence.py`：核心实现——三层 IR、四道闸门、契约构建（stdlib-only）。
- `scripts/selftest.py`：提交前自检（stdlib-only、frontmatter、端到端、无伪造分数）。
- `references/rules.md`：证据溯源、七要素、四道闸门、体裁/格式、不编造红线、冲突与复现。
- `references/checklist.md`：逐条自检清单、result.json 字段说明、降级策略。

脚本只用 Python 标准库；如环境无 Python，手工按 references 里的字段执行同样流程。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 任务完成度（15）：产物体裁/受众/格式符合 task.md，`artifact.path` 指向真实文件。
- [ ] 结果正确性（15）：每条事实都有 `locator`+`quote`；`unmapped`/`conflict` 单独说明；已完成与拟开展已分清。
- [ ] 结果完整性（5）：七要素齐全、厚度匹配，覆盖承诺题量/题型与六个方向。
- [ ] 结果可核验性（5）：`sources` 全、`change_log` 完整、来源清单含 SHA256，三级对应可复核。
- [ ] 质量增益（15）：四道闸门与一致性矩阵可观察可操作，而非泛泛“准确、完整”。
- [ ] 效率增益（5）：前 3 分钟落盘骨架 + 增量改进，减少返工，`change_log` 体现增量。
- [ ] 增益可验证性（5）：结论基于材料证据，不靠精选案例或自夸。
- [ ] 新增能力/方法改进（5+5）：把要求译成可观察判据、先校验后成稿、从分歧反推要素缺陷。
- [ ] 创新证据（5）：`uncertainty`/`version_boundary` 写清边界与失效条件。
- [ ] 调用易用/运行稳定（3+4）：新评分员按文档可独立跑通；脚本仅标准库、路径相对、重复运行一致。
- [ ] 异常处理（3）：题面歧义、答案缺失、材料超长/乱码、引用无法核实、格式冲突、模型拒答均有记录。
- [ ] 文档完整/可复用/分享价值（3+4+3）：SKILL.md+references 齐全，三层 IR、要素 schema、自检脚本可复用。

## 7. 降级与异常处理

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`；严禁补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ `uncertainty` 写明未能生成的项，保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不声称已全部核对。
- 模型拒答/截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 冲突与分歧 → 记录双方依据（各带 locator+quote），不擅自选一、不擅自改分。
- 空输入目录 → 退出码 0，写合法 `partial`/`insufficient_evidence` 结果，不抛异常。

## 8. 严禁事项

- 不预写正式测试答案/白板；不编造 benchmark 分数、实验数据、DOI、卷期页码、图表内容或运行结果。
- 不把文件名/领域标签当事实；不把未完成的 `in_progress`/`partial` 改成 `done`。
- 不提交隐藏测试或白板；平台提供或生成任何所需对照。
