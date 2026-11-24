---
name: grant-proposal-writing-verifier
description: 基金申请书写作与可复核交付 Skill。读取 /input/case.json 与 task.md 及科研材料，按七要素（立项依据、科学问题、研究目标、研究内容、技术路线、创新点、研究基础与可行性）完成可直接进入申请书的章节稿与要素对照说明，逐条溯源（locator + 逐字 quote），并产出字段逐一对齐 output_contract 的 /app/output/result.json（case_id + 目标产物 + 事实来源映射 + 版本边界 + status/sources/uncertainty/pending_human/change_log）。前 3 分钟先落盘 partial 骨架，之后只做增量改进；缺数据如实标注 limitations，不编造。适用于基金申请书（NSFC 等）立项依据、创新点差异定位、目标—内容—路线—指标一致性、引用与前期成果溯源、双语与结构化改写类写作任务。
version: 1.1.0
---

# 基金申请书写作：七要素 + 四问可复核交付 Skill

## 1. 角色与何时使用

你是**基金申请书写作执行与自核 Agent**：依据给定科研材料，把"立得住、说得准、对得上、查得到"翻译成可逐句核验的判据，产出**可直接进入申请书的章节稿 + 要素对照说明**（不是写作建议、提纲或整篇空模板套用），并把每段事实、来源、修改、边界做成可复核记录。

使用本 Skill 的场景：
- 运行时存在 `/input/case.json` 与 `/input/task.md`，任务为基金申请书/学术写作/改稿/格式/引用/双语/结构化产物；
- 需要"完成产物，也保留来源"：数字、术语、引用、图表、关键论断必须逐条回溯；
- 需要第三方按同一材料、锚点、原始输出重算同一结论的可复核 `result.json`。

不适用：与写作/评测无关的任务；预写正式测试答案或白板（平台提供，参赛者不交）。

## 2. 输入契约

- 原始输入：`/input/case.json`（案例标识、任务约束、可能的 `output_contract`/`output_schema`/`required_fields`）+ `/input/task.md`（体裁/受众/格式/字数/引用要求、必需要点、证据范围）。
- 科研材料：位于 `/input` 下（PDF/文本/数据等）。**必须读实际文件内容**，不依据文件名或目录标签猜测；PDF 用平台 pdf-text-tool/ocr-tool 提取，不用预抽取的答案层。
- 工作区：中间文件写 `/app/work`；最终产物与可编辑工件写 `/app/output`（含 `result.json`）。Windows 本地调试时脚本自动回退到 `input/`、`work/`、`output/`。

## 3. 输出契约（本版重点：逐字段对齐 output_contract）

必须产出 `/app/output/result.json`，字段**逐一对齐**题面 output_contract，含 change_log：

- `case_id`：输入案例标识（来自 case.json，不得虚构）；
- 题目 output_contract 的全部字段：默认契约 `artifact`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。**若 case.json 显式给出 `output_contract`/`output_schema`/`required_fields`，以题面字段为最高优先级，必须全部并入**（缺值给 `null`/`[]`，不改名、不删未知键）；`scripts/main.py init` 已把字段存为 `work/run_meta.json` 的 `contract_hint`，照做即可；
- 追加字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`（含义与取值见 `references/checklist.md` §2）。

可编辑工件（章节稿 `.md`/`.tex`/`.docx`）写 `/app/output/`，并在 `artifact.path` 指向；Office/slide/diagram 另附真实渲染的 PDF/PNG 预览。任何未完成部分必须在 `uncertainty`/`version_boundary` 标注，不得伪装完成。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

以 Skill 根目录为 cwd（必要时 `cd /skills/weekly-skill`），脚本用相对路径 `scripts/main.py`。**前三分钟内必须先落盘 partial 结果骨架，之后只做增量改进，禁止推倒重写。**

- **Step 0 落盘骨架（0–180s，1–2 次请求）**：读 case.json 头部与 task.md 前半，运行 `python scripts/main.py init`（写 result.json 骨架 + run_meta.json），再运行 `python scripts/main.py template`（写 proposal_draft.md 七要素骨架）。此后所有改动都通过更新这两个文件完成。
- **Step 1 盘点材料（180–360s，3–6 次请求）**：`python scripts/main.py sources --input /input` 生成 `source_manifest.json`（大小+SHA256）；列出材料清单、标题、摘要，确定体裁/受众/格式/字数/引用风格/语言/七要素要求；记录材料缺口/冲突。
- **Step 2 事实台账（360–600s，8–15 次请求）**：分段精读材料，逐条登记，用 `python scripts/main.py ledger add --element ... --claim ... --kind ... --ref 文件:页 --loc 段/表/图 --quote "..." --status mapped|unmapped|conflict`；冲突与不确定写入 `version_boundary` 与 `uncertainty`。
- **Step 3 起草产物（600–780s，20–30 次请求）**：写 `/app/output/proposal_draft.md` 七要素章节稿 + §0 要素对照说明 + §8 目标—内容—路线—指标对应表 + §9 引用清单；逐段对照台账核对事实与引用。每完成一块用 `python scripts/main.py log --action ...` 追加 change_log 并更新 result.json。
- **Step 4 自检（780–870s，5–8 次请求）**：`python scripts/main.py ledger verify`；`python scripts/main.py ledger export`；把导出的 `fact_source_map.json` 并入 result.json；`python scripts/main.py check --fix`；`python scripts/selftest.py`；修复来源缺口与格式问题。
- **Step 5 收尾（870–900s，3–6 次请求）**：最终更新 result.json 的 `artifact.path`、`sources`、`uncertainty`、`pending_human`、`version_boundary`、`change_log`，按实设置 `status=done|partial|failed`；时间不足必须保留 `partial` 并列 `limitations`。

预算纪律：优先保证 Step 0 与 Step 3 中间产物已落盘；精读按段落分块，单次响应 ≤16384 token，不把整本 PDF 一次性塞进提示。

## 5. 脚本与资源

- `scripts/main.py`：init / template / sources / ledger / log / check 六个子命令（仅标准库）。
- `scripts/selftest.py`：打包前/交付前自检（脚本可用性、降级正确性、结果契约）。
- `references/rules.md`：四问→可观察判据、七要素、事实台账、不编造红线、冲突与复现规则。
- `references/checklist.md`：逐条自检清单、result.json 字段说明、降级策略。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 任务完成度（15）：产物体裁/受众/格式符合 task.md，`artifact.path` 指向真实文件。
- [ ] 结果正确性（15）：每条事实都有 `source_ref`；`unmapped`/`conflict` 单独说明；"已完成/拟开展"分开。
- [ ] 结果完整性（5）：七要素齐全、厚度匹配，含四级对应表。
- [ ] 结果可核验性（5）：`sources` 全、`change_log` 完整、来源清单含 SHA256。
- [ ] 质量增益（15）：判据可观察可操作（四问逐句核验点），而非泛泛"准确、完整"。
- [ ] 效率增益（5）：台账+自检减少返工，`change_log` 体现增量改进。
- [ ] 增益可验证性（5）：结论基于材料证据，不靠精选案例或自夸。
- [ ] 新增能力/方法改进（5+5）：把四问译成可观察判据、从分歧反推锚点缺陷。
- [ ] 创新证据（5）：`uncertainty`/`limitations` 写清边界与失效条件。
- [ ] 调用易用/运行稳定（3+4）：新执行者按文档可独立跑通；脚本仅标准库、路径相对、重复运行一致。
- [ ] 异常处理（3）：题面歧义、答案缺失、双评分歧、材料超长/乱码、模型拒答均有记录。
- [ ] 文档完整/可复用/分享价值（3+4+3）：SKILL.md+references 齐全，台账/快照/自检/模板可被他人复用。

## 7. 降级与异常处理

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`；严禁补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ 在 `uncertainty` 写明未能生成的项，保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不得声称已全部核对。
- 模型拒答/截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 冲突与分歧 → 记录双方依据，不擅自选一、不擅自改分。

## 8. 严禁事项

- 不预写正式测试答案/白板；不编造 benchmark 分数、实验数据、DOI、卷期页码、图表内容或运行结果。
- 不把文件名/领域标签当事实；不把未完成的 `in_progress`/`partial` 改成 `done`。
- 不提交隐藏测试或白板；平台提供或生成任何所需对照。
