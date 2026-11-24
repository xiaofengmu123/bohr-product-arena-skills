---
name: litqa-plan
description: 文献研究质量评价与可追溯科研检索。围绕研究问题检索、筛选文献并输出候选集、筛选依据与遗漏说明；对重点文献产出"维度—判断—证据—等级"结构化评价表，每条判断用证据索引锚点(locator)与逐字quote溯源；以 plan.json / evidence_index.json / assessment_matrix.json 三个中间表示为可复核核心，支持确定性核验、分级与重放装配。适用于需要可定位证据、可解释分级、可双人复核的文献质量评价与科研检索场景。
version: 1.0.0
---

# 文献研究质量评价与可追溯检索（litqa-plan）

## 1. 角色与何时使用

本 Skill 把"检索筛选文献 + 评价文献研究质量"沉淀为一条可复核流程。核心产出是 **维度—判断—证据—等级** 表，而非摘要改写：每条判断对应原文可定位片段（`locator` + 逐字 `quote`），证据不足时明确弃权，不"看起来合理"地补缺；检索侧同时保留候选集、筛选依据与遗漏说明。

**适用**：给定科研问题，产出可追溯候选集、排序/筛选依据、遗漏说明，并对每篇重点文献给出结构化质量评价。**不适用**：一次性查证、纯事实核验；不替代同行评审、临床/政策决策或研究结论。

本版与泛化摘要方案的区别在于**中间表示与可复核**（详见 §3.2）：评价不直接产出一份结果，而是先落三个可持久化、可重放的中间产物，最终 `result.json` 由它们确定性装配；任何复核者都能从中间产物重算出同一等级。

## 2. 输入契约（先发现，再假设）

- 运行时输入目录：`/input/`，必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据响应等）路径与文件名不固定，先扫描识别，不假设键名。
- `case.json` 内含 `case_id` 与本题 `output_contract`（可能任意嵌套，键名大小写不敏感）。**必须先解析并原样回填其全部字段**：缺值给 `null`/`[]`，不改名、不删未知键。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 都降级，不崩溃）；单文件 >2MB 跳过并记录。
- PDF 用 `pdftotext` 抽取纯文本到 `/app/work/extracted/`（保留原始 PDF）；无工具则在 `degradations` 记录"未抽取文本"。
- 工作目录 `/app/work/`（中间产物），最终产物与可编辑交付件写 `/app/output/`。
- 事实依据是实际输入文件与原论文；不依据错误领域标签补造数据。

## 3. 输出契约

### 3.1 最终产物 `/app/output/result.json`

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "research_question": {}, "retrieval_plan": {},
  "candidates": [], "screening": [], "evidence_records": [], "omissions": [],
  "assessment": [ {"dimension","checkpoint","critical","verdict","judgment","evidence_id","locator","quote","basis","confidence","abstained","abstain_reason"} ],
  "grade": {"overall","derivation","dimensions","thresholds"},
  "sources": [ {"id","type","path_or_uri","locator","quote","note"} ],
  "uncertainty": [], "pending_human": [], "errors": [], "degradations": [], "limitations": [],
  "intermediates": {}, "verification": {}, "change_log": [ {"time_utc","event","detail"} ],
  "runtime": {"start_epoch","skeleton_epoch","finish_epoch"}
}
```

- `case_id` 必填；`status` 诚实：材料不足写 `insufficient_evidence`，不编造数据或结论。
- 主产物另含可读文本交付件 `/app/output/assessment.md`（评价表 + 分级推导 + 缺口说明）。若题目要求 Office/图件，同时给出真实渲染 PDF/PNG 预览；不要求则省略。
- 冲突信息不隐藏：同一指标多个来源不一致时全部记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

### 3.2 中间表示三件套（本版核心：可复核、可重放）

| 中间产物（`/app/work/`） | 内容 | 谁写 |
|---|---|---|
| `plan.json` | 研究问题 PICO 分解 + 检索计划（query/来源/日期/纳入排除）+ 检索记录（candidates/screening/omissions/sources/缺口） | Agent 增量填充 |
| `evidence_index.json` | 每文档段落/句子锚点：`locator`（如 `d0.p3.par2.s1`）+ 原文 + 字符偏移 + 规范化文本 | `scripts/litqa.py index` 确定性生成 |
| `assessment_matrix.json` | 量表实例：维度—判断—证据—等级 行式矩阵，证据用 `evidence_id`/`locator` 引用锚点 | Agent 逐行填写 |

派生产物（脚本确定性计算，不手写）：`verification_report.json`（quote 逐字核验）、`grade.json`（等级聚合与推导）。**result.json 由 `assemble` 从中间表示重放装配**，因此第二人可独立复核：重跑 `index → verify → grade → assemble` 应得同一等级。

## 4. 执行流程（分步骤 + 时间预算）

总预算：**900 秒 / 64 次模型请求 / 单响应 ≤16384 token**。**前 3 分钟内必须先落盘结果骨架**，之后只做增量改进，任何阶段结束都增量写盘。

| 阶段 | 时间 | 请求上限 | 步骤与输入/输出 |
|---|---|---|---|
| P0 落盘骨架 | 0–3 min | ≤6 | 执行 `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`。输出 `result.json`（partial）+ `plan.json`/`assessment_matrix.json` 模板。**必须最先完成**。 |
| P1 发现与索引 | 3–12 min | ≤28 | 扫描材料；PDF 抽文本到 `/app/work/extracted/`；`python3 -B scripts/litqa.py index --work /app/work --text-dir /app/work/extracted` 生成 `evidence_index.json`；识别研究问题与文献类型，填 `plan.json` 检索侧。输出 `candidates`/`screening`/`omissions`/`evidence_records`。 |
| P2 逐维度评价 | 12–21 min | ≤22 | 按 `references/rubric.md` 量表，逐文献、逐维度、逐条目填 `assessment_matrix.json` 行；每条带 `locator`+逐字 `quote`；跑 `python3 -B scripts/litqa.py verify --work /app/work` 核验并修正未命中项。 |
| P3 分级与装配 | 21–25 min | ≤6 | `python3 -B scripts/litqa.py grade --work /app/work` 聚合风险等级；汇总冲突/遗漏/不确定性/limitations；`python3 -B scripts/litqa.py assemble --input /input --work /app/work --output /app/output` 生成 `result.json` + `assessment.md`。 |
| P4 校验收尾 | 25–30 min | ≤2 | 执行 `python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work`；修正 error 级问题；设 `status` 最终值，写 `change_log`、`runtime.finish_epoch`。 |

原则：**骨架 → 中间表示 → 装配** 全程增量写盘；任何中途异常都不允许留下空产物，`status` 保持可解释的 partial/insufficient_evidence。

## 5. 自检清单（对应评分维度，交付前逐条勾选）

- [ ] 产出"维度—判断—证据—等级"表，非摘要改写；覆盖研究设计/证据充分性/偏倚风险/报告完整性四类维度 + 文献类型特有检查点。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`verify` 报告 `all_verified=true` 或未命中项已如实标弃权）。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段、`status`/`sources`/`uncertainty`/`change_log`。
- [ ] `scripts/main.py validate` 无 error 级问题；`scripts/selftest.py` 退出码 0。
- [ ] 全文不可得、仅摘要、图表不可解析、预印本/语言特殊、关键证据缺失等已如实标注，未编造。
- [ ] 分级有显式阈值与推导（`grade.derivation`），弃权条目在 `abstained`/`limitations` 中说明。

完整逐条清单见 `references/checklist.md`，量表定义与阈值见 `references/rubric.md`。

## 6. 降级策略

- **缺数据**：不足以支撑某维度时该条目判 `insufficient_evidence` 并置 `abstained=true`，`status` 相应降为 `insufficient_evidence`/`partial`，在 `limitations` 写清缺口，不补造。
- **缺软件**：只依赖 Python 标准库；`pdftotext` 不可用时保留原始 PDF，在 `degradations` 记录"未抽取文本"，仅评可得部分。
- **冲突/权限/接口异常**：写 `errors`/`pending_human`；候选集为空时如实 `status=insufficient_evidence` 并说明已完成与未完成部分。
- **重复运行**：确定性流程（无随机数、固定定位与阈值），同一输入结果一致。

## 7. 脚本（均在 SKILL 根目录下以相对路径运行）

- `scripts/main.py`：`skeleton` 立即落盘骨架 / `validate` 校验结构 / `--help`。
- `scripts/litqa.py`：`index` 锚点索引 / `verify` 逐字引文核验 / `grade` 阈值聚合分级 / `assemble` 中间表示重放装配。
- `scripts/selftest.py`：空目录 / 坏 JSON / 缺输入三场景 + 中间表示全链路 round-trip 自检。

## 8. 边界

不做"通用大而全"：一个 Skill 只解决"文献检索可追溯 + 质量评价可复核"一类任务；没有真实材料时输出"材料不足"而非示例填充；量表的维度、阈值、弃权规则与适用文献类型以 `references/rubric.md` 为准，脚本只做确定性核验与聚合，判断本身由 Agent 依据原文做出。
