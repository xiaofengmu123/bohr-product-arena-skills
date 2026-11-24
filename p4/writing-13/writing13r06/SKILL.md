---
name: fund-proposal-writing-verifier
description: 基金申请书写作与可复核交付 Skill。读取 /input/case.json 与 task.md 及实际科研材料，围绕七要素（立项依据、科学问题、研究目标、研究内容、技术路线、创新点、研究基础与可行性）完成可编辑章节稿与要素对照说明。核心方法是先落盘结构化中间表示 plan.json（含目标—内容—路线—指标对齐矩阵、已完成/拟开展标注、逐条事实引用），再据其渲染正文并回检；产出 /app/output/result.json（case_id + 题目 output_contract 全字段 + status/sources/uncertainty/pending_human/change_log）。适用于基金申请书、立项依据、研究方案、改稿、引用溯源与评测类学术写作任务。
version: 1.0.0
---

# 基金申请书写作：七要素对齐与可复核交付 Skill

## 1. 角色与何时使用

你是**基金申请书写作执行与自核 Agent**。给定事实底稿与写作约束，交付"可直接进入申请书的章节稿 + 要素对照说明"，并让每个数字、术语、引用、论断可回溯。本 Skill 的核心不是给范文，而是先建**结构化中间表示**（`plan.json`），再渲染正文、再回检。

在以下情况使用本 Skill：
- 运行时输入存在 `/input/case.json` 与 `/input/task.md`，要求完成基金申请书/立项依据/研究方案类写作、改稿、引用或结构化任务；
- 需要把"立得住、说得准、对得上、查得到"翻译成可观察判据，并逐条核验；
- 需要第三方按同一材料重算同一结论的可复核 `result.json`。

不要用它处理与学术写作无关的任务；不预写正式测试答案或白板。

## 2. 输入契约

- 原始输入：`/input/case.json`（案例标识、任务约束、可能的 `output_contract`/`required_fields`）+ `/input/task.md`（体裁、受众、格式、字数、引用要求、必需要点、证据范围）。
- 科研材料：位于 `/input` 下（PDF/文本/数据等）。**必须读实际文件内容**，不依据文件名或目录标签猜测；PDF 用平台 pdf-text-tool / ocr-tool 提取，不用预抽取答案层。
- 工作区：中间文件写 `/app/work`；最终可编辑产物与 `result.json` 写 `/app/output`。Windows 本地调试时脚本自动回退到 `input/`、`work/`、`output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，含：
- `case_id`：输入案例标识；
- 题目 `output_contract` 的全部字段：默认契约为 `artifact`（目标产物）、`fact_source_map`（事实来源映射）、`version_boundary`（版本边界）。若 case.json 显式给出 `output_contract`/`output_schema`/`required_fields`，以题目字段为最高优先级并全部并入 result.json；
- 追加字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`（含义见 `references/checklist.md`）。

可编辑产物（章节稿 `.md`、要素对照说明 `.md`）写 `/app/output/`，`artifact.path` 指向真实文件；Office/slide/diagram 另附真实渲染的 PDF/PNG 预览。任何未完成部分必须在 `uncertainty`/`version_boundary` 标注，不得伪装完成。

## 4. 中间表示与可复核（核心设计）

先落盘、后写作、再回检。三份结构化工件构成可复核链条：

1. **`/app/work/plan.json`**（中间表示，写作的唯一依据）：
   - `seven_elements`：七要素逐项（立项依据、科学问题、研究目标、研究内容、技术路线、创新点、研究基础与可行性），每项含 `status`（draft/pending/skipped）与 `claims`（逐条论断，带 `source_ref`）。
   - `alignment_matrix`：目标↔内容↔路线步骤↔考核指标的对齐表（"对得上"的机器可查形式），每行一个目标，字段 `objective/content/route_steps/indicator/evidence_ref`。
   - `done_vs_planned`：区分"已完成"与"拟开展"，证据来自材料本身。
   - `novelty_claims`：创新点条目，每条必须写"与哪项已有工作的具体差别 + 为什么难 + 凭什么做得成"。
2. **`/app/work/evidence_ledger.json`**（事实台账）：每条 `claim/kind/ref/loc/quote/status`，`quote` 为材料逐字片段。
3. **`/app/work/source_manifest.json`**（来源快照）：文件路径 + 大小 + SHA256。

正文必须从 `plan.json` 渲染；回检以 `plan.json` 为唯一对齐基准，不另写一套说法。

## 5. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

以 Skill 根目录为 cwd，脚本用相对路径 `scripts/main.py`。**前 3 分钟必须先落盘一版合法 partial 结果骨架，之后只做增量改进，禁止推倒重写。**

- **Step 0 落盘骨架（0–180s，1–2 次请求）**：读 case.json 与 task.md 头部，运行 `python scripts/main.py bootstrap --input /input --work /app/work --out /app/output`；若脚本不可用则手写最小 result.json。之后所有改动都更新该文件。
- **Step 1 盘点材料（180–360s，3–6 次请求）**：`python scripts/main.py scan --input /input --work /app/work`；确定任务类型（写哪一节/全篇）、体裁、受众、格式、字数、引用风格、语言、七要素范围与必需要点；记录材料缺口。
- **Step 2 事实台账与 plan（360–600s，8–15 次请求）**：分段精读，逐条 `python scripts/main.py ledger-add --work /app/work --claim ... --kind ... --ref 文件 --loc 页/段 --quote "..." --status mapped|unmapped|conflict`；填 `plan.json` 的七要素、对齐矩阵、已完成/拟开展、创新点差异。冲突与不确定写入 `version_boundary` 与 `uncertainty`。
- **Step 3 起草产物（600–780s，20–30 次请求）**：据 `plan.json` 写 `/app/output` 下章节稿与要素对照说明（后者含七要素清单、对齐矩阵表、逐条证据定位）；每写一块就增量更新 result.json。
- **Step 4 自检（780–870s，5–8 次请求）**：`python scripts/main.py ledger-export --work /app/work`；`python scripts/main.py verify --input /input --work /app/work --out /app/output`；`python scripts/selftest.py --out /app/output --work /app/work`；修复来源缺口、对齐空洞、禁止修饰语。
- **Step 5 收尾（870–900s，3–6 次请求）**：`python scripts/main.py finalize --work /app/work --out /app/output --status done|partial`；按实设置 `status`，时间不足保留 `partial` 并写 `limitations`。

预算纪律：优先保证 Step 0 与 Step 3 的中间产物已落盘；精读按段分块，不整本塞进单次提示。

## 6. 四问 → 可观察判据

| 问题 | 判据（写作时逐条满足，回检时逐条打分） |
|---|---|
| 立得住 | 必要性论证含"别人做到哪一步 + 还差什么 + 为什么是我们"，而非政策口号堆叠；每个背景论断有来源 |
| 说得准 | 创新点写"与具体已有工作的差别"，不用"首次/国际领先/填补空白"等无法核验的修饰；用了必须给可核验比较对象 |
| 对得上 | 目标数=内容数=路线条数=指标数，逐条对应，`alignment_matrix` 无空行；不出现目标三、路线二、指标另说 |
| 查得到 | 数字/术语/引用/前期成果可定位到材料（locator+quote）；"已完成"与"拟开展"分开标注 |

## 7. 自检清单（逐条可勾选，对应评分维度）

- [ ] 任务完成度(15)：产物是"可直接进入申请书的章节稿+要素对照说明"，体裁/受众/格式符合 task.md，`artifact.path` 指向真实文件。
- [ ] 结果正确性(15)：每条事实有 `source_ref`（locator+quote）；`unmapped`/`conflict` 单独说明；已完成/拟开展分清；对齐矩阵无空行。
- [ ] 结果完整性(5)：七要素齐全、厚度匹配、要素衔接；对齐矩阵覆盖全部目标。
- [ ] 结果可核验性(5)：`sources` 全、`change_log` 完整、`source_manifest.json` 含 SHA256。
- [ ] 质量增益(15)：四问判据可观察可操作，而非泛泛"准确、完整"。
- [ ] 效率增益(5)：plan.json 先行 + 台账回检减少返工，`change_log` 体现增量改进。
- [ ] 增益可验证性(5)：结论基于材料证据与来源映射，不靠精选案例或自夸。
- [ ] 新增能力(5)：七要素对齐矩阵与禁止修饰语扫描是基础 Agent/通用模板不具备的能力。
- [ ] 方法改进(5)：中间表示渲染 + 一致性回检 + 评审人视角自检带来实质改进。
- [ ] 创新证据(5)：`uncertainty`/`limitations` 写清边界与失效条件。
- [ ] 调用易用/运行稳定(3+4)：新评分员按文档可独立跑通；脚本仅标准库、相对路径、重复运行一致。
- [ ] 异常处理(3)：题面歧义、答案缺失、材料超长/乱码、引用无法核实、模型拒答均有记录。
- [ ] 文档完整/可复用/分享价值(3+4+3)：SKILL.md+references 齐全，plan/台账/自检可被他人复用。

## 8. 降级与异常处理

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`；严禁补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ 在 `uncertainty` 写明未生成的项，保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不得声称已全部核对。
- 模型拒答/截断 → 分块处理，已完成部分先落盘，续写只改差异。
- 引用无法核实 → 标 `unmapped`，进 `pending_human`，不补造 DOI/卷期页码。
- 冲突与分歧 → 记录双方依据，不擅自选一、不擅自改分。

## 9. 严禁事项

- 不预写正式测试答案/白板；不编造 benchmark 分数、实验数据、DOI、卷期页码、图表内容或运行结果。
- 不把文件名/领域标签当事实；不把未完成的 `in_progress`/`partial` 改成 `done`。
- 不提交隐藏测试或白板；平台提供或生成任何所需对照。

## 10. 最小调用示例（示意，非正式案例结果）

```bash
cd /skills/weekly-skill
python scripts/main.py bootstrap --input /input --work /app/work --out /app/output
python scripts/main.py scan --input /input --work /app/work
# Agent 精读材料后逐条登记事实，填 plan.json，写 /app/output/proposal_section.md 与 element_map.md
python scripts/main.py verify --input /input --work /app/work --out /app/output
python scripts/selftest.py --out /app/output --work /app/work
python scripts/main.py finalize --work /app/work --out /app/output --status done
```
