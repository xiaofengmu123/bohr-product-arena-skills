---
name: litqa-staged
description: 分阶段时间预算的文献研究质量评价与可追溯科研检索：围绕给定研究问题做源绑定检索（候选集、筛选依据、引用图、遗漏说明），并对每篇文献产出"维度—判断—证据—等级"结构化评价表，每条判断带可定位逐字证据（locator+quote），证据不足明确弃权，前3分钟先落盘partial结果骨架后只做增量改进。当需要评价文献研究设计与报告质量、定位证据片段、判定偏倚风险并给出可复核分级，或输出可追溯候选文献与筛选依据时使用。
version: 1.1.0
---

# litqa-staged：分阶段预算的可追溯文献质量评价

## 1. 角色与何时使用

本 Skill 把"源绑定检索 + 文献研究质量评价"沉淀为**分阶段、早落盘、可复核**的流程。核心产出不是摘要改写，而是：候选集 + 筛选依据 + 遗漏说明，以及每篇文献的 **维度—判断—证据—等级** 表。每条判断对应原文可定位片段（`locator` + 逐字 `quote`）；证据不足时弃权（`insufficient`），不允许"看起来合理"地补缺。

适用：给定科研问题，产出可追溯候选集、排序/筛选依据、遗漏说明，并对每篇重点文献做结构化质量评价与偏倚风险分级。

不适用：一次性查证；纯事实核验；替代同行评审、临床/政策决策或研究结论本身。

## 2. 输入契约（先发现，再假设）

- 运行时输入目录 `/input/`，必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据 JSON 等）路径与文件名不固定，先扫描识别，不假设键名。
- `case.json` 内含 `case_id` 与本题 `output_contract`（可能任意嵌套，键名大小写不敏感）。**必须先解析并原样回填其全部字段**：缺值给 `null`/`[]`，不改名、不删未知键。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 都要降级，不崩溃）；单文件 >2MB 跳过并记录到 `degradations`。
- **源绑定检索**：候选集只来自本地材料（Crossref/EuropePMC/publisher 响应、引用图、已给全文）；**不做 live 检索**，不虚构全文/引文/被引/状态。
- 工作目录 `/app/work/`（中间件），最终产物与可编辑交付件写 `/app/output/`。

## 3. 输出契约

必产出 `/app/output/result.json`，结构：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "research_question": {"stated","scope","note"},
  "retrieval": {"scope","query_log","reference_graph"},
  "assessment": [{"dimension","checkpoint","critical","polarity","verdict","judgment","locator","quote","basis","source_id"}],
  "grades": {"summary":{"overall","dimensions"},"rules"},
  "sources": [{"id","type","path_or_uri","locator","quote","note"}],
  "uncertainty": [], "conflicts": [], "pending_human": [],
  "errors": [], "degradations": [], "limitations": [],
  "change_log": [{"time_utc","event","detail"}],
  "budget": {"time_budget_s","request_budget","elapsed_s","requests_used","phase","checkpoints"},
  "runtime": {"start_epoch","skeleton_epoch","finish_epoch"}
}
```

- `case_id` 必填；`status` 诚实，材料不足写 `insufficient_evidence`，不编造。
- 判定值 `verdict` 取值受限：`confirmed/partial/absent/insufficient/not_applicable`；`polarity` 为 `favorable/adverse/neutral`；语义见 `references/rubric.md` §2。
- 主产物另写可读文本件 `/app/output/assessment.md`（评价表 + 证据锚点）。题目要求 Office/图件时同时给真实渲染 PDF/PNG 预览；不要求则省略。
- 冲突信息不隐藏：同一指标多来源不一致全部记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 4. 执行流程（分阶段预算 + 早落盘）

总预算：**900 秒 / 64 次模型请求**（单案例单次运行）。**硬约束：前 3 分钟必须落盘 partial 骨架，之后只做增量改进**；任何阶段结束都增量写盘，异常也不留空产物。

| 阶段 | 时间 | 请求上限 | 步骤与输入/输出 |
|---|---|---|---|
| **P0 早落盘** | 0–3 min | ≤6 | 立即执行 `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`，产出 partial 骨架（case_id + 契约字段 + 空数组）。**必须最先完成**；同时写 `/app/work/checkpoint.json` 记 `phase=P0`。 |
| P1 发现与检索 | 3–12 min | ≤28 | 扫描材料；把纯文本抽取件写到 `/app/work/text/`，用 `scripts/litqa.py locators --input /app/work/text --out /app/work/locators.json` 建稳定定位索引；识别研究问题与文献类型；构造 `query_log`/`candidates`/`screening`/`reference_graph`/`omissions`。 |
| P2 逐维度评价 | 12–21 min | ≤22 | 按 `references/rubric.md` 量表逐文献、逐维度、逐检查点判定；每条带 `locator`+`quote`；跑 `scripts/litqa.py verify --result /app/output/result.json --index /app/work/locators.json` 核验逐字存在。 |
| P3 分级与冲突 | 21–25 min | ≤6 | `scripts/litqa.py grade --result /app/output/result.json` 按阈值聚合维度/总体等级；汇总冲突、遗漏、不确定性、limitations；写 `assessment.md`。 |
| P4 校验收尾 | 25–30 min | ≤2 | `python3 -B scripts/main.py validate --input /input --output /app/output`；修 error 级问题；设最终 `status`，写 `change_log`、`budget.elapsed_s/requests_used`、`runtime.finish_epoch`。 |

增量机制（本版的明确取舍）：

1. **checkpoint 日志** `/app/work/checkpoint.json`：每阶段结束追加 `{phase,time_utc,event,detail}`，中断后可据此续跑，不重做已完成阶段。
2. **预算护栏** `/app/work/budget.json`：记录已用时间/请求。临近上限（如请求 ≥56 或时间 ≥810s）时**主动降级**：停止新增检查点，把未评项判 `insufficient`，`status=partial`，在 `limitations` 写清"因预算收尾"。
3. **确定性**：无随机数、固定排序与阈值，同一输入重复运行结果一致（时间戳字段除外）。

## 5. 自检清单

完整清单见 `references/checklist.md`，交付前逐条勾选。核心项：

- [ ] 产出"维度—判断—证据—等级"表，非摘要改写；四类维度 + 文献类型特有检查点覆盖。
- [ ] 每条非弃权 `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 结果 `fail=0`）。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段、`status/sources/uncertainty/change_log`。
- [ ] `scripts/main.py validate` 无 error 级问题；`scripts/selftest.py` 六场景全过（退出码 0）。
- [ ] 全文不可得/仅摘要/图表不可解析/预印本或语言特殊/关键证据缺失已如实标注，未编造。

## 6. 降级策略

- 缺数据：某维度材料不足以支撑时，该条目判 `insufficient`，`status` 相应降为 `partial`/`insufficient_evidence`，`limitations` 写清缺口，不补造。
- 缺软件：仅依赖 Python 标准库；`pdftotext` 不可用时保留原始 PDF，`degradations` 记"未抽取文本"，只评可得部分。
- 冲突/权限/接口异常：写 `errors`/`pending_human`；候选集为空时 `status=insufficient_evidence`，说明已完成与未完成部分。
- 预算耗尽：按 §4 预算护栏主动收尾，不静默越界。
- 重复运行：确定性流程，同一输入结果一致。

## 7. 脚本（均在 SKILL 根目录下以相对路径运行）

- `scripts/main.py`：`skeleton` 立即落盘骨架 / `validate` 校验结构。用法见 §4 与 `--help`。
- `scripts/litqa.py`：`locators` 段落/句子定位 / `verify` 逐字引文核验 / `grade` 阈值聚合分级。
- `scripts/selftest.py`：空目录/坏 JSON/缺输入/确定性/逐字核验/负例六场景自检。

## 8. 交付边界

本 Skill 输出"可复核的维度判定 + 分级理由 + 缺口清单"，不输出"收/拒"或替代结论；检索只依赖本地材料与引用图，不做 live 检索。量表、阈值、定位语法与弃权规则的完整定义见 `references/rubric.md`；自检逐条勾选见 `references/checklist.md`。
