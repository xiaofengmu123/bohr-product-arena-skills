---
name: paper-structure-logic-optimization
description: 论文结构与逻辑优化 Skill。以真实稿件为唯一事实依据，逐句拆解章节功能、抽取论点—论据链、检测跨章节重复与结论越界，输出可定位（locator+逐字引文）、可执行（移动/合并/拆分/收窄）的结构诊断与修改方案，最终写入 /app/output/result.json 及可编辑产物。适用于论文结构诊断、论证链核查、结论边界校对与改稿对照，不适用于需要新实验或凭空补写内容的场景。
version: 1.0.0
---

# 论文结构与逻辑优化

## 1. 角色与何时使用

本 Skill 在以下场景启用：任务要求对一篇完整科研稿件（或其中章节）进行结构与逻辑诊断，交付作者可直接执行的修改方案，并保留每条判断的来源。四个目标逐条兑现：诊得准（问题定位到章节/段落/句子并归类）、改得动（给出"移动到哪、与哪段合并、从哪拆分、哪句收窄"而非另起改写稿）、链得通（研究问题—方法—结果—结论形成闭环，每条论断有论据）、说得清（每条判断可指回原文位置与依据）。

本 Skill 的突破口为"结构级诊断 + 确定性证据抽取"：脚本先机械抽取章节、候选论点、数值与越界标记，模型只核对与判断、不凭空生成；同一输入两次运行证据抽取结果逐字节一致。六类结构问题为：研究问题、章节结构、论证链、段落关系、重复内容、结论边界（详见 references/structure-taxonomy.md）。

不适用：需要新实验、新数据或新增文献检索来填补核心空白的任务；纯语言润色（不涉及结构判断）的任务。此时如实报告材料缺口，不补造内容。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（案例元数据、任务说明、材料清单、可能的 output_contract）与 `/input/task.md`。字段名与文件名不假设固定：扫描目录识别，`case.json`/`task.md`/`materials/` 等变体都要能容错。
- 材料文件：`/input/` 下按清单提供的 PDF、图片、Markdown、表格、统计输出、文本或文献。PDF 可能没有预解析层，必须实际读取文件内容，不得凭文件名或领域标签猜测。文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级不崩溃）；单文件超 2MB 跳过并记录。
- 工作目录：`/app/work`（中间产物）；最终产物目录：`/app/output/`。
- 脚本位于本 Skill 根目录 `scripts/`，统一以 Skill 根目录为工作目录用相对路径调用（若 Harness 从 `/app/work` 启动，先 `cd /skills/weekly-skill`）。仅依赖 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，合法 JSON），并满足：

- `case_id`：取自 case.json 的 `case_id` 或 `id`，原样保留；缺省为 `null`。
- 题目 output_contract 的全部字段：从 case.json 的 `output_contract`（或任务说明规定字段）读取键名，逐字段回填，缺值给 `null`/`[]`，不改名、不删未知键。若未显式给出，按默认契约输出：`diagnosis`（可编辑结构诊断报告文本）、`edit_plan`（可执行修改动作数组）、`source_map`（事实与来源映射）、`version_boundary`（修改差异、人工确认项、材料缺口与异常）、`artifacts`（可编辑文件与预览路径）。
- `status`：`complete | partial | failed | insufficient_evidence | input_error` 之一，如实标注。
- `sources`：数组，逐条 {claim_id, claim, locator, quote, confidence}；`locator` 写"文件相对路径+章节/段落/行号/图表编号"，`quote` 为原文逐字引文（证据硬要求，不得转写或概括）。
- `uncertainty`：数组，记录材料不足、冲突、无法判断项及原因。
- `pending_human`：数组，列出需人工确认项（材料缺口、冲突取舍、结论边界无法判定）。
- `change_log`：数组，按时间顺序 {time, action, status}，至少含骨架落盘与每次增量修改。
- `limitations` / `errors` / `degradations` / `runtime`：如实记录限制、错误、降级与耗时。

可编辑产物（Markdown/JSON/CSV）写到 `/app/output/`；docx/pptx/图类产物另存 PDF/PNG 预览供审阅。`result.json` 允许多次增量覆盖，每次写入保持 JSON 合法、字段完整。`diagnosis.md` 与 `edit_plan.md` 作为可继续编辑的正文产物落盘，与 result.json 内嵌字段同源。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

硬性要求：开始后 3 分钟内必须落盘一版 result.json 骨架（status=in_progress），之后只做增量改进；任何失败都不允许不落盘。

- 步骤 0（0–1 分钟）：读 `/input/case.json` 与 `/input/task.md`，确认 case_id、体裁、受众、格式、output_contract。运行 `python scripts/main.py skeleton` 生成骨架 result.json（status=in_progress，各字段空值，change_log 记第一条）。
- 步骤 1（1–6 分钟）：盘点材料。运行 `python scripts/main.py evidence` 生成 `/app/work/evidence.json` 与 `/app/work/manifest.json`：按文本/表格/PDF/图片分类，抽取章节标题、候选论点句、数值/单位/统计量、候选越界句、近重复句簇；PDF 用 pdftotext 或渲染+OCR 读取，图片需 render/OCR。标记无法直接读取的文件。
- 步骤 2（6–10 分钟）：证据核对与定位。逐条核对候选论点、数值、图表与越界标记，确认 locator 与逐字引文；为每条将写入正文的判断建立 {claim_id, locator, quote}。增量写回 result.json 的 sources。
- 步骤 3（10–14 分钟）：结构诊断与修改方案。按六类维度逐条产出诊断项 {id, dimension, severity, problem, locator, quote, edit_action}；edit_action 写"移动到哪/与哪段合并/从哪拆分/哪句收窄"，覆盖六类中稿件实际存在问题的类别（缺项若确非主要问题则如实说明）。输入：evidence.json + 稿件；输出：result.json 的 diagnosis/edit_plan 与 diagnosis.md/edit_plan.md。
- 步骤 4（14–15 分钟）：自检与定稿。运行 `python scripts/main.py verify`（校验字段完整、数值可回溯、locator+quote 齐全、局限如实标注）；修复 FAIL/WARN；`python scripts/main.py finalize --status complete|partial|failed` 置 status 并追加 change_log。

剩余时间用于增量改进：优先补来源映射与局限，不推翻已落盘内容。某步缺材料立即降级（见第 6 节），不空转。

## 5. 自检清单（逐条勾选，对应评分维度）

实际效果（40 分）：
- [ ] 已产出可编辑、可复核的结构诊断报告与可执行修改方案（任务完成度）。
- [ ] 每条判断与原文一致：无虚构缺陷、无漏掉真实断裂、未把风格偏好当结构错误（正确性）。
- [ ] 六类维度（研究问题、章节结构、论证链、段落关系、重复内容、结论边界）均有覆盖或如实说明缺项原因（完整性）。
- [ ] 每条判断含 locator + 逐字 quote，可独立复核（可核验性）。

使用增益（25 分）：
- [ ] 起草前已用脚本盘点材料与候选论点/数值，而非凭记忆（质量/效率）。
- [ ] 骨架先行、增量改进，返工次数记录于 change_log（增益可验证）。

创新增益（15 分）：
- [ ] 论点—论据映射、跨章节重复检测、结论边界比对由脚本提供候选锚点，模型只核对（新增能力）。
- [ ] 章节功能拆解、论点抽取、重复识别、越界检查遵循统一规则文件（方法改进）。

可靠体验（10 分）：
- [ ] 触发条件明确、脚本 `--help` 可运行、路径有效、重复运行结果一致（易用/稳定/确定性）。
- [ ] 缺章节/无参考文献/图公式缺失/格式混乱/稿件截断时已标注并降级，未编造（异常处理）。

生态贡献（10 分）：
- [ ] 说明、依赖（仅标准库）、配置、范围、限制齐全（文档完整）。
- [ ] 分类规则、检查清单与脚本可被他人复用（可复用性）。

## 6. 降级策略

- 缺文件/缺软件（无 pdftotext/OCR/渲染器）：在 uncertainty/limitations 中如实标注"未提供/不可用"，相应字段留空并说明，status 置 partial 或 insufficient_evidence；绝不伪造读数、软件状态或科学数据。
- 图表/公式读数不清、单位不明、误差棒或显著性边界无法确定：不猜测，标为待确认（uncertainty/pending_human），正文用"[待确认：xxx]"占位或改用稳健表述。
- 稿件被截断、无参考文献、章节缺失：在 limitations 中说明影响范围，只对已提供部分下判断，缺失部分不臆测。
- 引用找不到或文献不全：只写已有文献支持的论断，缺失部分标注"无文献支持"。
- 运行失败或超时：保留已落盘 partial result.json（含 status、sources、uncertainty、change_log），说明失败原因。

## 7. 脚本调用（仅 Python 标准库）

- `python scripts/main.py skeleton`：生成骨架 result.json。
- `python scripts/main.py evidence`：盘点材料并抽取候选章节/论点/数值/越界/近重复 → /app/work/evidence.json。
- `python scripts/main.py verify`：校验 result.json 契约与可回溯性 → /app/output/verify_report.json。
- `python scripts/main.py finalize --status complete|partial|failed`：置状态、追加 change_log 并校验。
- `python scripts/selftest.py`：包级自检（frontmatter、脚本可运行、确定性双跑一致、契约完整）。

环境变量可覆盖默认路径：CASE_JSON、TASK_MD、INPUT_DIR、WORK_DIR、OUTPUT_DIR、RESULT_JSON。禁止用脚本或其他方式伪造评测分数或运行结果。

## 8. 确定性约定

同一输入两次运行，`evidence.json` 与 `result.json` 的 `sources`/`diagnosis`/`edit_plan` 内容必须一致：不依赖随机数、不依赖模型采样（模型仅核对脚本候选）、列表按 locator 稳定排序、`evidence.json` 不含时间戳。时间类字段（change_log.time、runtime）允许不同。交付前必须跑一次 `python scripts/selftest.py`，其确定性双跑检查通过才可提交。

## 9. 最小调用示例（示意，非正式案例结果）

```bash
cd /skills/weekly-skill
python scripts/main.py skeleton      # 立即落盘骨架
python scripts/main.py evidence      # 抽取候选证据
# 模型核对 /app/work/evidence.json，填入 diagnosis / edit_plan / sources
python scripts/main.py verify        # 校验契约与可回溯性
python scripts/main.py finalize --status complete
```

示意输出（节选，仅供说明接口，非正式答案）：

```json
{"case_id": "example-1", "status": "complete",
 "edit_plan": [{"id": "D1", "dimension": "conclusion_boundary",
   "severity": "high", "locator": "paper.md#L15", "quote": "X 导致 Y",
   "edit_action": "将 '导致' 收窄为 'X 与 Y 相关'，因果关系留待进一步实验"}]}
```

