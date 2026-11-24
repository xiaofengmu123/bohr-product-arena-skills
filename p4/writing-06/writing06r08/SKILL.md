---
name: paper-structure-review
description: 科研论文"结构与逻辑优化"Skill。围绕研究问题、章节结构、论证链、段落关系、重复内容、结论边界六类结构问题，把诊断定位到章节/段落/句子并分类，产出作者可直接执行的编辑动作（移动/合并/拆分/收窄/补衔接/补局限），建立论点—论据映射与逐字来源定位，最终写入 /app/output/result.json。适用于整篇稿件、单章或摘要的结构诊断；区别于语言润色，只处理结构级缺陷。
version: 1.0.0
---

# 论文结构与逻辑优化

## 1. 角色与何时使用

本 Skill 对一篇科研稿件做结构诊断与可执行改稿建议：把结构问题定位到章节/段落/句子、给出缺陷分类，产出作者可直接执行的编辑动作（移动到哪、与哪段合并、从哪拆、哪句收窄、哪里补衔接或补局限），并用论点—论据映射保证"研究问题—方法—结果—结论"闭环可复核。

何时使用：
- 输入包含整篇稿件、单章、摘要或改稿任务，要求"结构/逻辑优化、改得动、链得通、说得清"。
- 需要结构诊断报告 + 修改方案 + 来源映射的写作、审稿、编辑任务。

何时不用：
- 需要新实验、新数据或新检索填补核心空白的任务——此时如实报告材料缺口，不补造。
- 纯语言润色（用词、语法、风格）而不涉及结构——本 Skill 排除风格偏好，不把"风格"当"结构错误"。
- 不基于给定材料的自由写作、非科研文本。

突破口：**论证链与论据对应**。以逐条"论断—论据"映射为骨架，驱动其余五类结构检查；所有判断必须指回原文位置与逐字引用（说得清）。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（case_id、任务说明、材料清单、可能含 output_contract）与 `/input/task.md`。
- 材料文件：`/input/` 下的 PDF、Markdown、LaTeX、Word、图片、CSV/JSON 等。PDF 可能没有预解析层，必须实际读取内容，禁止凭文件名或领域标签猜测（错误领域标签是已知坑）。
- 工作目录：`/app/work`（中间产物）；最终产物目录：`/app/output/`。
- 脚本位于 Skill 根目录 `scripts/`，用相对路径 `scripts/main.py` 调用（工作目录为 Skill 根目录；若 Harness 从 `/app/work` 启动，先 `cd /skills/weekly-skill`）。
- 文本按 `utf-8-sig` 读取并容错（BOM/坏编码/坏 JSON 降级，不崩溃）；单文件 >2MB 跳过并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 合法 JSON）。字段：

- `case_id`：取自 case.json 的 `case_id` 或 `id`，原样保留。
- 题目 output_contract 全部字段：从 case.json 的 `output_contract` 读取键名并逐字段回填；未知键不改名、不删除；缺值给 `null` 或 `[]`。若未显式给出，按默认契约输出：
  - `diagnosis`：结构诊断报告（可编辑 Markdown；六类逐条 findings，见 references/structure-taxonomy.md）。
  - `edit_plan`：可执行修改方案（action_id、action_type、target_locator、before_quote、instruction、rationale、status）。
  - `claim_evidence_map`：论点—论据映射表（claim_id、claim、evidence_refs、evidence_status、gap_note）。
  - `source_map`：事实与来源映射（数字、术语、引用、图表、论断）。
  - `version_boundary`：修改差异、人工确认项、材料缺口与异常。
- `status`：`complete` / `partial` / `insufficient_evidence` / `input_error` 之一（诚实标注）。
- `sources`：数组 {claim_id, claim, locator, quote, source_ref, confidence}；quote 为材料逐字片段。
- `uncertainty`：数组 {id, item, reason, fallback}。
- `pending_human`：数组 {id, item, reason}（需人工确认项）。
- `change_log`：数组 {time, action, status}，按时间顺序，至少含骨架落盘与每次增量修改。
- `errors` / `degradations`：异常与降级记录（可为空数组）。

可编辑产物（Markdown/JSON）写到 `/app/output/`；result.json 允许多次增量覆盖，每次写入保持 JSON 合法、字段完整。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

硬性要求：开始后 3 分钟内必须落盘一版 result.json 骨架（status=in_progress，各字段空值，change_log 记第一条）；之后只做增量改进，任何失败都不允许不落盘。

- 步骤 0（0–1 分钟）读输入：读 `/input/case.json` 与 `/input/task.md`，确认 case_id、体裁、受众、格式、output_contract。运行 `python scripts/main.py --init` 生成骨架。输入：case.json/task.md；输出：`/app/output/result.json` 骨架。
- 步骤 1（1–4 分钟）盘点材料：运行 `python scripts/main.py --scan` 生成 `/app/work/evidence.json`，分类文件、抽取章节、候选论断、数字/单位/引用/图表格/统计量锚点，标记无法直接读取的文件（PDF/图需 render/OCR）。输入：/input 文件；输出：evidence.json + 材料清单。
- 步骤 2（4–8 分钟）结构诊断：逐条核对候选论断与论据，跑六类检查（脚本给候选锚点，模型只核对不凭空生成）；每类缺陷给 locator + 逐字 quote + defect_type + severity；增量写回 result.json 的 diagnosis 与 sources。
- 步骤 3（8–12 分钟）写修改方案：把每条缺陷转成可执行编辑动作（move/merge/split/narrow/add_transition/add_citation/delete_duplicate/add_limitation）；每条动作含 target_locator、before_quote、instruction、rationale；输出 edit_plan + claim_evidence_map。
- 步骤 4（12–14 分钟）闭环与边界核对：检查研究问题是否在结论回环，结论是否超出数据/条件范围、是否把相关性写成因果；填 version_boundary 与 pending_human。
- 步骤 5（14–15 分钟）自检定稿：运行 `python scripts/selftest.py` 校验字段完整、逐条可回溯、局限如实标注；修复 FAIL/WARN；status 置 complete/partial/insufficient_evidence/input_error。

剩余时间增量改进：优先补来源映射、编辑动作的可执行性与 pending_human；不推翻已落盘内容。缺材料立即降级（见第 6 节），不空转。

## 5. 自检清单（逐条勾选，对应评分维度）

实际效果（40 分）：
- [ ] diagnosis + edit_plan 已产出，为可编辑、可执行文本（任务完成度 15）。
- [ ] 每条判断与原文相符，未虚构缺陷、未漏真实断裂、未把风格偏好当结构错误（正确性 15）。
- [ ] 六类（研究问题/章节结构/论证链/段落关系/重复内容/结论边界）均已扫过；缺项显式标"未发现/不适用"，且不遗漏主要问题（完整性 5）。
- [ ] 每条判断指回 locator + 逐字 quote，作者可独立复核（可核验性 5）。

使用增益（25 分）：
- [ ] 骨架先行、增量改进，change_log 记录每次落盘与返工（增益可验证 5）。
- [ ] 诊断到可执行动作一步到位，减少作者返工轮次（效率增益 5）。
- [ ] 与基础 Agent 相比，诊断定位到句子并给出动作而非空泛原则（质量增益 15）。

创新增益（15 分）：
- [ ] 论点—论据映射由脚本抽取候选锚点，模型只核对不凭空生成（新增能力 5）。
- [ ] 六类缺陷分类 + 编辑动作词汇表统一复用（方法改进 5）。
- [ ] 明确与语言润色工具的边界：只处理结构，不改写全文（创新证据 5）。

可靠体验（10 分）：
- [ ] 触发条件明确、脚本可用、相对路径有效（调用易用 3）。
- [ ] 空输入/坏 JSON/坏编码/缺章节/缺图表/稿件截断均降级不崩溃（运行稳定 4）。
- [ ] 缺材料时如实标注 limitations，不编造（异常处理 3）。

生态贡献（10 分）：
- [ ] 说明、依赖（仅标准库）、配置、范围、限制、维护齐全（文档完整 3）。
- [ ] 六维分类法、编辑动作词汇表、论点—论据映射表可复用（资源可复用 4）。
- [ ] 沉淀结构问题分类与改稿前后对照模板（分享价值 3）。

## 6. 降级策略

- 缺文件/缺软件（无 OCR、无 LaTeX、无渲染器）：在 uncertainty/limitations 标注"未提供/不可用"，对应字段留空并说明，status=partial；绝不伪造读数、软件状态或科学数据。
- 材料不足/截断/缺图表缺公式：不猜测，标 pending_human；诊断正文用"[待确认：xxx]"占位或改用稳健表述。
- 引用找不到或文献不全：只写已有文献支持的论断，缺失标注"无文献支持"。
- 无稿件正文（只有标题/摘要）：无法做结构诊断，status=insufficient_evidence，sources/uncertainty 如实说明。
- 运行失败/超时：保留已落盘的 partial result.json（含 status、sources、uncertainty、change_log），说明失败原因。
- 材料冲突（同一论断两处不一致）：两处都记录并标 source_conflict，不隐去。

## 7. 脚本调用（仅 Python 标准库）

- `python scripts/main.py --init`：生成 result.json 骨架。
- `python scripts/main.py --scan`：盘点材料并抽取候选论断/证据锚点 → `/app/work/evidence.json`。
- `python scripts/main.py --full`：init + scan + 诊断候选落盘 + 契约填充 → `/app/output/result.json`。
- `python scripts/selftest.py`：自检（frontmatter、脚本可运行、空输入/坏 JSON、字段完整、体积限制）→ 自检报告 JSON（默认临时目录，可 `--out /app/output/selftest_report.json` 指定）。

参数覆盖：`--case`、`--input`、`--work`、`--out`；环境变量：CASE_JSON、TASK_MD、INPUT_DIR、WORK_DIR、OUTPUT_DIR、RESULT_JSON。禁止用脚本或其他方式伪造评测分数或运行结果；模型/案例/运行时/预算由平台记录。

## 8. 评分维度对齐（本版侧重点）

本版把评分维度逐条映射到流程步骤与自检项（详见 `references/scoring-alignment.md`），使每一步都有可验证的评分落点：实际效果→步骤 2–4 的诊断/改稿/溯源与自检项 §5 实际效果；使用增益→步骤 0/5 的骨架先行与 change_log（§5 使用增益）；创新增益→脚本候选锚点 + 六维分类 + 编辑动作词汇表（§5 创新增益）；可靠体验→步骤 1 容错读取 + 第 6 节降级（§5 可靠体验）；生态贡献→第 7 节文档 + references 复用资源（§5 生态贡献）。与"结果/讨论章节写作"类参考包不同，本 Skill 不做整段改写，只交付"定位到句子的可执行编辑动作 + 逐字溯源"。
