---
name: litqa-evidence-index
description: 文献研究质量评价与可追溯科研检索：围绕研究问题检索筛选文献，先建立段落级证据索引，再产出"维度—判断—证据—等级"结构化评价表，每条判断带可定位逐字证据，支持冲突记录、遗漏说明、阈值分级与确定性复核。当需要评价文献研究设计/证据充分性/偏倚风险/报告完整性、定位证据片段、给出可复核风险分级或输出可追溯候选文献与筛选依据时使用。
version: 1.1.0
---

# 文献研究质量评价与可追溯检索（litqa-evidence-index）

## 1. 角色与何时使用

本 Skill 把"检索筛选文献 + 评价文献研究质量"沉淀为**先建索引、再逐条判定、后阈值分级**的可复核流程。适用：给定科研问题，产出可追溯候选集、排序/筛选依据、遗漏说明，并对每篇重点文献给出结构化质量评价。核心产物是 **维度—判断—证据—等级** 表：每条判断对应原文可定位片段（`locator` + 逐字 `quote`），证据不足时明确弃权，不补造。

不适用：一次性查证；纯事实核验；替代同行评审、临床/政策决策或研究结论。

## 2. 输入契约（先发现，再假设）

- 运行时输入目录 `/input/`，必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据响应等）路径与文件名不固定，需先扫描识别，不假设键名。
- `case.json` 含 `case_id` 与本题 `output_contract`（可能任意命名/嵌套，键名大小写不敏感）。**必须先解析并原样回填其全部字段**：缺值给 `null`/`[]`，不改名、不删未知键。
- 文本按 `utf-8-sig` 优先、`utf-8/gbk/latin-1` 降级读取；单文件 >2MB 跳过并记录；坏 JSON/坏编码降级不崩溃。
- 工作目录 `/app/work/`（中间产物：证据索引、核验报告），最终产物与可编辑交付件写 `/app/output/`。
- 事实依据是实际输入文件与原论文；不依据错误领域标签补造数据。

## 3. 输出契约

必产出 `/app/output/result.json`，结构：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "assessment": [{"dimension","checkpoint","critical","verdict","judgment","bias","locator","quote","basis"}],
  "candidates": [], "screening": [], "evidence_records": [], "omissions": [],
  "sources": [{"id","type","path_or_uri","locator","quote","note"}],
  "uncertainty": [], "change_log": [{"time_utc","event","detail"}],
  "grade": {"overall","dimensions","rationale","thresholds_applied"},
  "errors": [], "degradations": [], "pending_human": [], "limitations": [],
  "verification": {"status","summary"}, "runtime": {"start_epoch","skeleton_epoch","finish_epoch"}
}
```

- `case_id` 必填；`status` 诚实：材料不足写 `insufficient_evidence`，不编造数据或结论。
- 主产物还有可读交付件 `/app/output/assessment.md`（评价表 + 证据锚点 + 分级理由）。题目要求 Office/图件时同时给真实渲染 PDF/PNG 预览，否则省略。
- 冲突信息不隐藏：同一指标多来源不一致时全部记录并标 `source_conflict`；缺失写 `pending_human` 或 `not_reported`；不虚构引用、DOI、数值。

## 4. 执行流程（分阶段 + 时间预算）

总预算：**900 秒 / 64 次模型请求**（单案例单次运行）。**前 3 分钟必须先落盘 partial 骨架，之后只做增量改进**，每阶段结束都写盘。

| 阶段 | 时间 | 请求上限 | 步骤（脚本在 SKILL 根目录以相对路径运行） |
|---|---|---|---|
| P0 落盘骨架 | 0–3 min | ≤6 | `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`。输出：`result.json`（partial 骨架，含 case_id + 契约字段 + 空结构）+ `assessment.md` 骨架。**必须最先完成**。 |
| P1 建立索引 | 3–9 min | ≤16 | `python3 -B scripts/main.py index …`：扫描材料、抽取文本、建立段落证据索引 `work/index.json`（稳定 ID `rel_path#pN`）。识别研究问题与文献类型，记录检索范围与筛选依据。输出：索引 + `evidence_records`/`candidates`。 |
| P2 逐维度评价 | 9–20 min | ≤30 | 按 `references/rubric.md` 量表，逐文献、逐维度、逐条目判定；每条带 `locator`+逐字 `quote`。以 `work/assessment_patch.json` 分批 `merge` 增量合并，再 `verify` 抽查引文。输出：填充 `assessment`。 |
| P3 分级与冲突 | 20–25 min | ≤8 | `python3 -B scripts/main.py grade …` 按阈值聚合风险等级；汇总冲突、遗漏、不确定性、limitations。输出：`assessment.md` 终稿 + 更新 `result.json`。 |
| P4 校验收尾 | 25–30 min | ≤2 | `python3 -B scripts/main.py validate …`；修正 error 级问题；设 `status` 终值，写 `change_log`、`runtime.finish_epoch`。输出：最终 `result.json`。 |

原则：每阶段增量写盘；任何中途异常不留下空产物，`status` 保持可解释的 partial/insufficient_evidence/input_error。

## 5. 证据索引与定位规范（中间表示）

- `locator` 一律指向 `work/index.json` 中的稳定段落 ID（`rel_path#pN`），由 `index` 命令一次性生成；`verify`/`grade`/`validate` 复用同一索引，保证核验一致。
- `quote` 必须逐字存在于所标段落内（脚本做归一化子串匹配）；引文跨越段落、或仅摘要/图表不可解析时，如实标 `证据不足`，不跨段拼接。
- 定位优先级：全文段落级 > 摘要句子级 > 图表编号；同一判断可给多个 `locator`（多证据并列时在 `basis` 说明主证据）。
- 每条 `assessment` 的 `verdict`/`judgment` 由原文支撑；`critical` 取 `关键/重要/一般`。

## 6. 判定词表与分级

- `verdict` ∈ {支持, 部分支持, 不支持, 证据不足, 无法评价}；`bias` ∈ {明确存在, 可能存在, 未见迹象, 证据不足}。
- 整体 `grade` ∈ {低偏倚风险, 中偏倚风险, 高偏倚风险, 证据不足}，阈值见 `references/rubric.md`；由 `grade` 命令按关键性加权聚合，`rationale` 说明命中哪条阈值。
- 弃权规则：关键证据缺失、全文不可得、图表不可解析时，该条目判 `证据不足`，不写"看似合理"的判断。

## 7. 自检清单

完整清单见 `references/checklist.md`，交付前逐条勾选。核心项：

- [ ] 产出"维度—判断—证据—等级"表，非摘要改写；四类维度覆盖 + 文献类型特有检查点。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`verify` 抽查通过，mismatch=0）。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段、`status`/`sources`/`uncertainty`/`change_log`。
- [ ] `validate` 无 error 级问题；`self-test` 通过（空目录/坏 JSON/缺输入三场景退出码 0）。
- [ ] 全文不可得、仅摘要、图表不可解析、预印本/语言特殊、关键证据缺失已如实标注，未编造。

## 8. 降级策略

- 缺数据：材料不足以支撑某维度时，该条目判 `证据不足`，`status` 相应降为 `insufficient_evidence` 或 `partial`，`limitations` 写清缺口，不补造。
- 缺软件：只依赖 Python 标准库；`pdftotext` 不可用或 PDF 抽取失败时，保留原始 PDF，`degradations` 记录"未抽取文本"，仅评可得部分。
- 冲突/权限/接口异常：写 `errors`/`pending_human`；候选集为空时如实 `status=insufficient_evidence` 并说明已完成与未完成部分。
- 重复运行：确定性流程（无随机数、固定排序、固定阈值、单调时间戳），同一输入结果一致。

## 9. 脚本（均在 SKILL 根目录下以相对路径运行）

- `scripts/main.py`：薄 CLI——`skeleton` / `index` / `verify` / `grade` / `merge` / `validate` / `self-test`。
- `scripts/core_impl.py`：数据面——输入发现、契约回填、证据索引、原子写盘。
- `scripts/rubric.py`：规则面——量表定义、引文核验、分级聚合、结构校验。

架构取舍：中间表示（证据索引）在数据面与规则面之间解耦；证据核验（verify）先于分级（grade）与结构校验（validate）执行，保证"先证后评"。
