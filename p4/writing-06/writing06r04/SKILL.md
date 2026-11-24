---
name: paper-structure-diagnosis
description: 科研论文"结构与逻辑"诊断 Skill。对完整稿件执行六维结构诊断（研究问题、章节结构、论证链、段落关系、重复内容、结论边界），把问题定位到具体章节/段落/句子并给出可执行编辑动作（移动/合并/拆分/收窄等），产出论点—论据映射表与逐条可回溯的来源证据，最终写入 /app/output/result.json。适用于完整稿件的结构诊断、论证链核对、结论边界检查、跨章节冗余识别与可复核修改方案交付；不做语言润色、不编造缺陷。
version: 1.0.0
---

# 论文结构与逻辑诊断

## 1. 角色与何时使用

本 Skill 用于：给定一篇（或部分）科研稿件及其事实材料，产出**可复核的结构诊断与可执行的修改方案**，而非语言润色稿。核心交付回答四个问题：

- 诊得准：把问题定位到具体章节、段落乃至句子，并给出结构问题类别。
- 改得动：给出可执行编辑动作——移动到哪、与哪段合并、从哪里拆分、哪句论断需收窄——而非另起一篇改写稿。
- 链得通：核对研究问题—方法—结果—结论是否闭环，每条论断是否有对应论据。
- 说得清：每条判断指回原文位置与逐字依据，作者可判断采纳/部分采纳/驳回。

适用：完整稿件的结构诊断、论证链核对、结论边界检查、跨章节冗余识别、修改方案交付。
不适用：语言润色、需要新实验或新数据才能填补的空白（此时如实报告缺口，不补造）。

**本 Skill 的突破口为"论点—论据映射 + 六维结构诊断"**：先抽取论断并建立论据来源映射（链得通），再按六维分类定位问题并给编辑动作（诊得准、改得动），每条判断附 locator + 逐字 quote（说得清）。与语言润色类工具的边界是：本 Skill 只诊断结构与逻辑，不改写文风；缺证据时不虚构缺陷。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（案例元数据、任务说明、材料清单、可能的 output_contract）与 `/input/task.md`。
- 材料文件：`/input/` 下按清单提供的稿件 PDF、Markdown/LaTeX、图片、表格、统计输出、参考文献；PDF 可能无预解析层，必须实际读取文件内容，不能凭文件名或领域标签猜测。
- 工作目录：`/app/work`（中间产物）；最终产物目录：`/app/output/`。
- 脚本位于本 Skill 根目录 `scripts/`；若 Harness 从 `/app/work` 启动，先 `cd /skills/weekly-skill` 再以相对路径调用。
- 环境变量可覆盖默认路径：CASE_JSON、TASK_MD、INPUT_DIR、WORK_DIR、OUTPUT_DIR、RESULT_JSON、EVIDENCE_JSON、VERIFY_REPORT。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，合法 JSON），结构如下：

- `case_id`：取自 case.json 的 `case_id` 或 `id` 或 `armiId`，原样保留。
- 题目 output_contract 的全部字段：从 case.json 的 `output_contract`（若存在）读取键名并原样回填全部键；缺值给 null/[]，不改名、不删未知键。若未显式给出 output_contract，按默认契约输出（完整 JSON Schema 见 references/structure-taxonomy.md 第 8 节）：
  - `diagnosis`：结构诊断主产物（结构化 JSON，见第 6 节）。
  - `source_map`：事实与来源映射（数组，逐条带 claim_id、claim、locator、quote、confidence）。
  - `version_boundary`：修改差异、人工确认项、材料缺口（对象）。
  - `artifacts`：可编辑文件与渲染预览路径（对象）。
- `status`：`complete` / `partial` / `failed` 之一。
- `sources`：数组，逐条 {claim_id, claim, locator, quote, confidence}；locator 写"文件+章节/段落/图表编号"，quote 为逐字片段。
- `uncertainty`：数组，逐条记录材料不足、冲突、未确认项及原因。
- `pending_human`：数组，逐条记录需要作者确认的判断。
- `limitations`：数组，逐条记录本方法局限（如：未读取的图片、截断的稿件、无参考文献）。
- `change_log`：数组，按时间顺序 {time, action, status}，至少含骨架落盘与每次增量修改。

`result.json` 允许多次增量覆盖更新，每次写入保持 JSON 合法、字段完整。可编辑产物（Markdown）写到 `/app/output/`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

硬性要求：开始后 **3 分钟内必须先落盘一版 result.json 骨架**（status=in_progress），之后只做增量改进；任何失败都不允许不落盘。

- 步骤 0（0–1 分钟）：读 `/input/case.json` 与 `/input/task.md`，确认 case_id、体裁、受众、格式、output_contract。运行 `python scripts/skeleton.py` 生成骨架（status=in_progress，output_contract 字段置空值，change_log 记第一条）。输入：case.json/task.md；输出：/app/output/result.json 骨架。
- 步骤 1（1–5 分钟）：盘点材料。运行 `python scripts/evidence.py` 生成 /app/work/evidence.json，分类文件并抽取候选章节锚点、候选数值、单位、统计量与论断线索，标记无法直接读取的文件（图片/OCR、超限、截断）。输入：/input；输出：evidence.json 与材料清单。
- 步骤 2（5–10 分钟）：抽取论断与论据映射。从稿件正文逐条抽取论断（claim），为每条标注位置与支撑论据来源；无法找到论据的标 `missing`，循环或跳跃的标 `circular`/`leap`。逐条核对候选数值与图表。增量写回 result.json 的 source_map 与 sources。输入：evidence.json + 稿件；输出：result.json.source_map/sources。
- 步骤 3（10–12 分钟）：六维诊断。按第 5 节六维框架逐维检查，每个问题给 issue_id、维度、严重度、位置、problem_type、逐字证据、edit_action；**某维未见问题时写"未见结构问题"及其依据，不虚构缺陷**。增量写回 diagnosis.issues 与 dimension_coverage。输入：sources + 稿件；输出：result.json.diagnosis。
- 步骤 4（12–14 分钟）：写可执行修改方案与版本边界。为每条 issue 补 edit_action（动作类型+目标位置+最小修改建议），填 pending_human、limitations、version_boundary、artifacts。输入：diagnosis；输出：result.json 主产物。
- 步骤 5（14–15 分钟）：自检与定稿。运行 `python scripts/verify.py` 校验契约与可回溯性，修复 FAIL/WARN；status 置 complete/partial/failed。输入：result.json；输出：/app/output/result.json 终稿 + /app/output/verify_report.json。

剩余时间增量改进：优先补来源映射、局限与待确认项，不推翻已落盘内容。某步缺材料立即降级（第 8 节），不空转。

## 5. 六维诊断框架

六个维度（问题类型与判定细则见 references/structure-taxonomy.md）：

1. 研究问题与贡献主张对齐
2. 章节结构与叙事顺序
3. 论证链与论据对应
4. 段落关系
5. 重复内容
6. 结论边界与表述尺度

每条 issue 必须包含：issue_id、dimension、severity（critical/major/minor）、location（章节/段落/句子）、problem_type、problem_description、evidence（locator + 逐字 quote）、edit_action（action_type + target_location + instruction）、confidence（high/medium/low）、acceptance（accept/partial/reject，供作者复核）。

**反虚构规则**：只报告有逐字证据支撑的结构问题；某维未发现真实问题就明确写"该维未见结构问题"并给依据；不把风格偏好当结构错误；不因材料缺失而编造缺陷。

## 6. diagnosis 主产物结构

`diagnosis` 为结构化 JSON 对象：

- `summary`：{manuscript_identity, overall_assessment, issue_counts{critical,major,minor}}
- `issues`：数组，元素见第 5 节 issue 字段。
- `claim_evidence_map`：数组，逐条 {claim_id, claim, claim_location, evidence_refs, strength(sufficient/insufficient/missing/circular/leap), verdict}
- `dimension_coverage`：对象，六维各给 {checked, status("有结构问题"/"未见结构问题"/"证据不足"), note}
- `edit_plan`：按优先级排序的可执行修改清单。

完整 JSON Schema 见 references/structure-taxonomy.md 第 8 节。

## 7. 自检清单（逐条勾选，对应评分维度）

实际效果（40 分）：

- [ ] 对完整稿件产出结构诊断与可执行修改方案，而非另起改写稿（任务完成度）。
- [ ] 每个问题定位到章节/段落/句子，与原文逐字一致，不虚构缺陷、不漏真实断裂、不把风格偏好当结构错误（正确性）。
- [ ] 六维均检查并记录结论（问题/未见问题/证据不足），缺项如实说明（完整性）。
- [ ] 每条判断与每个论断都能指回 locator + 逐字 quote（可核验性）。

使用增益（25 分）：

- [ ] 诊断前已用脚本盘点材料与候选锚点，而非凭记忆（质量/效率）。
- [ ] 骨架先行、增量改进，返工记录于 change_log（增益可验证）。

创新增益（15 分）：

- [ ] 论点—论据映射表逐条建立，缺失/循环/跳跃显式标注（新增能力）。
- [ ] 六维问题分类 + 可执行编辑动作类型（move/merge/split/narrow/...）统一规范（方法改进）。

可靠体验（10 分）：

- [ ] 触发条件明确、脚本可运行、相对路径有效（易用/稳定）。
- [ ] 缺章节、无参考文献、图表公式缺失、格式混乱、稿件截断均已标注并降级，未编造（异常处理）。

生态贡献（10 分）：

- [ ] 说明、依赖（仅 Python 标准库）、配置、范围、限制齐全（文档完整）。
- [ ] 六维分类、检查清单、论点—论据映射表可复用（可复用性）。

## 8. 降级策略（缺数据/缺软件不编造）

- 缺文件/缺软件（无 OCR、无渲染器、无 pdftotext）：在 limitations 与 uncertainty 中如实标注"未提供/不可用"，相应字段留空并说明，status 置 partial；绝不伪造读数或软件状态。
- 图表读数不清、单位不明、统计量无法确定：不猜测，标为待确认（pending_human），正文用"[待确认：xxx]"占位或改用稳健表述。
- 无参考文献：只基于稿件自身论断写来源映射，标注"无参考文献支持"。
- 稿件被截断/缺章节：在 limitations 记录截断位置，只诊断已读取部分，不补造缺失章节。
- 某维证据不足：该维 coverage 标"证据不足"，不写具体 issue。
- 运行失败或超时：保留已落盘的 partial result.json（含 status、sources、uncertainty、change_log），说明失败原因。

## 9. 脚本调用（仅 Python 标准库）

- `python scripts/skeleton.py`：生成骨架 result.json（早落盘）。
- `python scripts/evidence.py`：盘点材料并抽取候选锚点/数值 → /app/work/evidence.json。
- `python scripts/verify.py`：校验 result.json 契约与可回溯性 → /app/output/verify_report.json。
- `python scripts/selftest.py`：空输入/坏 JSON 自检，验证脚本可运行。

禁止用脚本或其他方式伪造评测分数或运行结果。

## 10. 最小调用示例（示意，非正式案例结果）

输入：`/input/case.json`（含 case_id）、`/input/task.md`、`/input/paper.md`（示例稿件）。

```bash
cd /skills/weekly-skill
python scripts/skeleton.py      # 落盘 /app/output/result.json 骨架
python scripts/evidence.py      # 生成 /app/work/evidence.json
# （Agent 按第 4 节步骤 2–4 增量写入诊断内容）
python scripts/verify.py        # 生成 /app/output/verify_report.json
```

示意输出片段（仅为接口形状，不冒充正式案例结果）：

```json
{
  "case_id": "example-case-000",
  "status": "partial",
  "diagnosis": {
    "summary": {"manuscript_identity": "示例稿件", "overall_assessment": null, "issue_counts": {"critical": 0, "major": 1, "minor": 0}},
    "issues": [
      {"issue_id": "I01", "dimension": "论证链与论据对应", "severity": "major",
       "location": {"section": "3. 结果", "paragraph": "第 2 段", "sentence_quote": "结论为 X。"},
       "problem_type": "论证断裂", "problem_description": "该论断未给出支撑论据。",
       "evidence": {"locator": "paper.md#3-结果-第2段", "quote": "结论为 X。"},
       "edit_action": {"action_type": "narrow", "target_location": "paper.md#3-结果-第2段",
                        "instruction": "将该句收窄为'在本实验条件下，X 呈趋势性变化（p=0.07）'。", "suggested_revision": null},
       "confidence": "high", "acceptance": "accept"}
    ],
    "claim_evidence_map": [],
    "dimension_coverage": {"论证链与论据对应": {"checked": true, "status": "有结构问题", "note": "见 I01"}},
    "edit_plan": [{"priority": 1, "issue_id": "I01", "action": "收窄结论表述"}]
  },
  "source_map": [{"claim_id": "C01", "claim": "结论为 X。", "locator": "paper.md#3-结果-第2段", "quote": "结论为 X。", "confidence": "high"}],
  "version_boundary": {"diffs": [], "human_confirm": [], "material_gaps": []},
  "artifacts": {"diagnosis_md": null, "preview": null},
  "sources": [{"claim_id": "C01", "claim": "结论为 X。", "locator": "paper.md#3-结果-第2段", "quote": "结论为 X。", "confidence": "high"}],
  "uncertainty": [{"item": "全文其余维度", "reason": "示例输入仅含单段", "status": "pending"}],
  "pending_human": [],
  "limitations": [{"scope": "示例", "detail": "示意输出，非正式案例结果"}],
  "change_log": [{"time": "2026-01-01T00:00:00+08:00", "action": "skeleton", "status": "in_progress"}]
}
```
