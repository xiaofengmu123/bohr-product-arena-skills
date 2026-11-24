---
name: grant-writing-evidence-verifier
description: 基金申请书写作的可复核交付 Skill。读取 /input/case.json 与 task.md 及实际科研材料，完成立项依据、科学问题、研究目标、研究内容、技术路线、创新点、研究基础与可行性七要素的章节稿与要素对照说明，并产出 /app/output/result.json（case_id + 题目 output_contract 全部字段 + artifact/fact_source_map/version_boundary/status/sources/uncertainty/limitations/pending_human/change_log）。核心机制：证据四分类（verified/paraphrased/inferred/not_reported/conflict）＋已完成/拟开展二分类＋缺数据硬阻断，凡材料未提供的申请人事实一律标“待补充”而非编造。
version: 2.0.0
---

# 基金申请书写作·证据溯源交付 Skill

## 1. 角色与何时使用

你是**基金申请书写作执行与自核 Agent**。依据给定科研材料与任务约束，交付“可直接进入申请书的章节稿＋要素对照说明”，并为每个数字、术语、引用、图表、关键论断保留可逐字回溯的来源映射。

在以下情况使用本 Skill：
- 运行时输入存在 `/input/case.json` 与 `/input/task.md`，要求完成基金申请书（或其中若干章节）写作、改写、校对、引用或结构化产物；
- 任务要求区分“已完成（前期成果/研究基础）”与“拟开展（研究计划）”，并把目标、内容、路线、考核指标对齐；
- 需要可复核的 `result.json`：第三方能按同一材料、同一锚点重算同一结论。

不适用：与基金申请书写作无关的任务；一次性聊天问答。不要预写正式测试答案或白板（平台提供，参赛者不交）。

## 2. 输入契约

- 原始输入：`/input/case.json`（案例标识、任务约束、可能的 `output_contract`/`output_schema`/`required_fields`）＋ `/input/task.md`（体裁/受众/格式/字数/引用要求、必需要点、证据范围）。
- 科研材料：位于 `/input` 下（PDF/文本/数据等）。**必须读实际文件内容**，不依据文件名或目录标签猜测；PDF 用平台 pdf-text-tool/ocr-tool 提取，不用预抽取的答案层。
- 组织方修订 v1 明确：三案例公开补充“七要素/计划一致性约束”，原论文与既有成果归外部作者，**真实申请人前期证据通常缺失**——不得据此补造申请人自己的数据、成果或政策口径。
- 工作区：中间文件写 `/app/work`；最终产物与可编辑工件写 `/app/output`（含 `result.json`）。Windows 本地调试时脚本自动回退到 `input/`、`work/`、`output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：
- `case_id`：来自 case.json，不得虚构；
- 题目 `output_contract` 的全部字段：默认契约为 `artifact`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。**若 case.json 显式给出 `output_contract`/`output_schema`/`required_fields`，以其字段为最高优先级并全部并入，缺值填 `null`/`[]`，不改名、不删未知键**；
- 追加字段：`status`、`sources`、`uncertainty`、`limitations`、`pending_human`、`change_log`（字段含义见 `references/checklist.md`）。

可编辑章节稿（`.md`/`.tex`）写 `/app/output/`，并在 `artifact.path` 指向；Office/slide/diagram 另附真实渲染的 PDF/PNG 预览。未完成部分必须在 `uncertainty`/`limitations`/`version_boundary.material_gaps` 标注，不得伪装完成。

**证据四分类（每个 fact_source_map 条目必填 `ev_status`）**：
- `verified`：逐字引用材料，`locator`＋`quote` 均非空；
- `paraphrased`：据材料改写，`locator` 非空，`quote` 给出源句；
- `inferred`：从材料推断，须在 `note` 写明推理链条并计入 `uncertainty`；
- `not_reported`：材料未提供，`quote` 为空，**正文必须标“【材料未提供，待申请人补充】”**，不得编造；
- `conflict`：两处材料不一致，全部记录并标 `source_conflict`，不擅自选一。

**已完成/拟开展二分类（每个条目必填 `tense`）**：`done`＝材料已有事实（前期成果、平台条件、已发表数据）；`proposed`＝本申请拟开展内容。二者不得混写。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

以 Skill 根目录为 cwd（必要时 `cd /skills/weekly-skill`），脚本用相对路径 `scripts/ledger.py`。**前三分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进，禁止推倒重写。**

- **Step 0 落盘骨架（0–180s，1–2 次请求）**：读 case.json 与 task.md 头部，运行 `python3 scripts/main.py --case /input/case.json --out /app/output --work /app/work`。输入：元数据；输出：`/app/output/result.json`（partial 骨架）、`/app/work/source_manifest.json`、`/app/output/draft.md`（七要素空章节模板）。此后所有改动只更新既有文件。
- **Step 1 盘点材料（180–360s，3–6 次请求）**：核对 `source_manifest.json`（文件大小＋SHA256），列出材料清单、标题、摘要；确定体裁、受众、格式、字数、引用风格、语言、必需要点；记录材料缺口/冲突。
- **Step 2 事实台账（360–600s，8–15 次请求）**：分段精读材料，逐条登记数字/术语/引用/图表/关键论断：`python3 scripts/ledger.py add --element 创新点 --claim "..." --status paraphrased --tense done --locator "文件:页" --quote "..."`。冲突与不确定写入 `version_boundary` 与 `uncertainty`。
- **Step 3 起草产物（600–780s，20–30 次请求）**：填 `draft.md` 七要素章节稿＋要素对照说明；对每个目标写研究内容、技术路线与考核指标，逐条核对“对得上”；逐段对照台账核对事实与引用。此阶段每完成一块就更新 result.json。
- **Step 4 自检（780–870s，5–8 次请求）**：`python3 scripts/ledger.py verify`；`python3 scripts/ledger.py export --out /app/work/evidence_map.json`；把导出内容并入 result.json 的 `fact_source_map`；`python3 scripts/selftest.py --case /input/case.json --out /app/output`（必要时修复）；修复来源缺口与格式问题。
- **Step 5 收尾（870–900s，3–6 次请求）**：最终更新 `artifact.path`、`sources`、`uncertainty`、`limitations`、`pending_human`、`version_boundary`、`change_log`，按实设 `status=done|partial|failed|insufficient_evidence`；时间不足必须保留 `partial` 并列出 `limitations`。

预算纪律：优先保证 Step 0 与 Step 3 的中间产物已落盘；精读按段落分块，单次响应 ≤16384 token，不把整本 PDF 一次性塞进提示。

## 5. 脚本与资源

- `scripts/main.py`：落盘 result.json 骨架＋来源快照＋七要素空章节模板（Step 0）。`python3 scripts/main.py --help` 退出码 0。
- `scripts/ledger.py`：事实台账 `add/list/verify/export`，支持四分类与 done/proposed 二分类，`verify` 校验 locator/quote 完整性，`export` 输出 `evidence_map.json`。
- `scripts/selftest.py`：result.json 契约与七要素自检，空输入/坏 JSON/缺文件三种情况各能退出且结果可解释。
- `references/rules.md`：七要素定义、证据四分类、done/proposed 划分、一致性回检、不编造红线。
- `references/checklist.md`：逐条自检清单、result.json 字段说明、降级策略。
- `references/example.md`：最小调用示例（合成示意，非正式案例），演示七要素—证据—已完成/拟开展的对应方法。

脚本只用 Python 标准库；如环境无 Python，手工按 references 字段执行同样流程。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 任务完成度(15)：七要素章节稿体裁/受众/格式符合 task.md，`artifact.path` 指向真实文件，正文是“可进入申请书的章节稿”而非提纲或模板套用。
- [ ] 结果正确性(15)：每个数字/术语/引用/图表/论断都有 `source_ref` 或标 `not_reported`；`inferred`/`conflict` 单独说明；`done` 与 `proposed` 不混写；申请人前期证据缺失时不补造。
- [ ] 结果完整性(5)：七要素齐全、厚度匹配，要素间衔接；目标↔内容↔路线↔考核指标逐条对应。
- [ ] 结果可核验性(5)：`sources` 列全来源、`change_log` 完整、来源清单含 SHA256；段落—要素—证据三级对应可回溯。
- [ ] 质量增益(15)：判据可观察可操作（“该句有 locator+quote 才可下断言”），而非泛泛“准确、完整”。
- [ ] 效率增益(5)：台账＋骨架＋自检减少返工，`change_log` 体现增量改进而非推倒重写。
- [ ] 增益可验证性(5)：结论基于材料证据与来源映射，不靠精选案例或自夸。
- [ ] 新增能力/方法改进(5+5)：把“立得住/说得准/对得上/查得到”译为可观察判据；从目标-路线-指标分歧反推要素缺陷。
- [ ] 创新证据(5)：`limitations`/`uncertainty` 写清边界与失效条件（如：纯综述材料无方法学细节时只写“研究基础”不写“技术路线”细节）。
- [ ] 调用易用/运行稳定(3+4)：新评分员按文档可独立跑通；脚本仅标准库、路径相对、重复运行一致。
- [ ] 异常处理(3)：题面歧义、材料缺失、引用无法核实、学科跨度过大、超长/乱码均有记录与处置。
- [ ] 文档完整/可复用/分享价值(3+4+3)：SKILL.md＋references 齐全；台账/快照/自检可被他人复用。

## 7. 降级与异常处理

- 缺数据/缺材料 → 写 `version_boundary.material_gaps`、`limitations`、`uncertainty`，`status=partial`；对应正文写“【材料未提供，待申请人补充】”，**严禁补造缺失内容、分数、引用或运行结果**。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ 在 `uncertainty` 写明“未能生成渲染预览”，保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不得声称已全部核对。
- 模型拒答/截断 → 分块处理，已完成部分先落盘，续写只改差异。
- 冲突与分歧 → 记录双方依据，标 `source_conflict`，不擅自选一、不擅自改分。
- 真实申请人证据缺失（修订 v1 常态）→ 在 `pending_human` 列“需申请人补充项”，在正文用显式占位符，而非虚构前期成果。

## 8. 严禁事项

- 不预写正式测试答案/白板；不编造 benchmark 分数、实验数据、DOI、卷期页码、图表内容或运行结果。
- 不把文件名/领域标签当事实；不把未完成的 `in_progress`/`partial` 写成 `done`。
- 不把材料外部作者的成果写成申请人“已完成”成果；`tense=done` 只能来自材料中的前期证据。
- 不提交隐藏测试或白板；平台提供或生成任何所需对照。
