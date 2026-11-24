---
name: litqa-audit
description: 文献研究质量评价与可追溯科研检索（确定性版）：围绕给定研究问题检索筛选文献，产出"维度—判断—证据—等级"结构化评价表，每条判断带可定位逐字证据（locator + quote），支持缺证据弃权、分级可解释与重复运行结果逐字节一致。当需要评价文献研究设计与报告质量、定位证据片段、判定偏倚风险并给出可复核分级，或需要输出可追溯候选文献与筛选依据时使用。
version: 1.1.0
---

# 文献研究质量评价与可追溯检索（litqa-audit）

## 1. 角色与何时使用

本 Skill 把"检索筛选文献 + 评价文献研究质量"沉淀为可复核、可复现流程。核心产出是**维度—判断—证据—等级**表，而非摘要改写：每条判断对应原文可定位片段（`locator` + 逐字 `quote`），证据不足时弃权（`insufficient_evidence`），不允许"看起来合理"地补缺。相较通用摘要、公开报告清单与编辑初筛，本 Skill 的价值在三点：量表驱动（`references/rubric.md`）、证据锚定到句子/表格级、分级可解释且由阈值汇总。

不适用：一次性查证；纯事实核验；替代同行评审、临床/政策决策或研究结论。

## 2. 输入契约（先发现，再假设）

- 运行时输入目录 `/input/`，必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据响应等）路径与文件名不固定，先扫描识别，不假设键名。
- `case.json` 内含 `case_id` 与本题 `output_contract`（可能任意嵌套，键名大小写不敏感，如 `output_contract`/`outputContract`/`contract`/`interface`）。**必须解析并原样回填其全部字段**：缺值给 `null`/`[]`，不改名、不删未知键。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 都降级，不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（中间产物），最终产物与可编辑交付件写 `/app/output/`。
- 事实依据是实际输入文件与原论文；不依据错误领域标签补造数据。

## 3. 输出契约

必产出 `/app/output/result.json`，结构：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "research_question": null,
  "assessment": [ {"dimension","checkpoint","critical","verdict","judgment","locator","quote","basis"} ],
  "grades": [ {"dimension","grade","rationale","supporting_checkpoints"} ],
  "overall_grade": null,
  "candidates": [], "screening": [], "evidence_records": [], "omissions": [],
  "sources": [ {"id","type","path_or_uri","locator","quote","note"} ],
  "uncertainty": [], "pending_human": [], "errors": [], "degradations": [], "limitations": [],
  "change_log": [ {"step","event","detail"} ],
  "runtime": { "run_id","deterministic","phases": [] }
}
```

- `case_id` 必填；`status` 诚实：材料不足写 `insufficient_evidence`，不编造数据或结论。
- `verdict` 取值受限：`clear_risk | possible_risk | no_risk_indicator | insufficient_evidence`。
- 主产物还要有可读文本交付件 `/app/output/assessment.md`（评价表 + 证据锚点）。若题目要求 Office/图件，同时给真实渲染 PDF/PNG 预览；不要求则省略。
- 冲突信息不隐藏：同一指标多源不一致全部记录并标 `source_conflict`。不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 4. 执行流程（分步骤 + 时间预算）

总预算：**900 秒 / 64 次模型请求**（单案例单次运行）。**前 3 分钟必须先落盘骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求上限 | 步骤与输入/输出 |
|---|---|---|---|
| P0 落盘骨架 | 0–3 min | ≤6 | 输入 `/input`。执行 `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`。输出 `result.json`（partial 骨架，含 case_id + 契约字段 + 空数组 + run_id）。**此步最先完成，任何异常都保留该骨架。** |
| P1 发现与读取 | 3–12 min | ≤28 | 扫描材料；抽取文本后执行 `python3 -B scripts/litqa.py locators --input <材料目录> --work /app/work` 生成稳定段落索引；识别研究问题与文献类型；记录检索范围与筛选依据。输出 `/app/work/evidence_index.json` + `candidates`/`screening`/`evidence_records`。 |
| P2 逐维度评价 | 12–21 min | ≤22 | 按 `references/rubric.md` 逐文献、逐维度、逐条目判定（每条带 `locator`+`quote`）；把评价片段写成 JSON，用 `python3 -B scripts/litqa.py absorb --result /app/output/result.json --fragment <片段>` 确定性合并。 |
| P3 分级与冲突 | 21–25 min | ≤6 | 执行 `python3 -B scripts/litqa.py grade --result /app/output/result.json` 聚合维度/总体等级；用 `scripts/litqa.py verify --index /app/work/evidence_index.json --result /app/output/result.json --out /app/output/verification_report.json` 核验逐字引文；汇总冲突、遗漏、不确定性、limitations。输出 `assessment.md` + 更新 `result.json`。 |
| P4 校验收尾 | 25–30 min | ≤2 | 执行 `python3 -B scripts/main.py validate --input /input --output /app/output`；修正 error 级问题；设 `status` 为最终值，写 `change_log` 与 `runtime.phases`。输出最终 `result.json`。 |

原则：每阶段结束都增量写盘；任何中途异常都不留空产物，`status` 保持可解释的 partial/insufficient_evidence。增量改进要可追溯：`change_log` 按固定顺序幂等 upsert，重复运行不漂移。

## 5. 自检清单

完整清单见 `references/checklist.md`，机器自检见 §7。核心项：

- [ ] 产出"维度—判断—证据—等级"表，非摘要改写；四类维度（研究设计/证据充分性/偏倚风险/报告完整性）+ 文献类型特有检查点。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 全部通过）。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段、`status`/`sources`/`uncertainty`/`change_log`。
- [ ] `scripts/main.py validate` 无 error 级问题；`scripts/selftest.py` 七项全过（S1–S7）。
- [ ] 全文不可得、仅摘要、图表不可解析、预印本/语言特殊、关键证据缺失等已如实标注，未编造。

## 6. 降级策略

- 缺数据：材料不足以支撑某维度时，该条目判 `insufficient_evidence`，`status` 降为 `insufficient_evidence` 或 `partial`，在 `limitations` 写清缺口，不补造。
- 缺软件：只依赖 Python 标准库，无外部依赖；若 `pdftotext` 等工具不可用，保留原始 PDF 并在 `degradations` 记录"未抽取文本"，仅评可得部分。
- 冲突/权限/接口异常：写 `errors`/`pending_human`；候选集为空时如实 `status=insufficient_evidence` 并说明已完成与未完成部分。
- 重复运行：确定性流程（无随机数、无墙钟时间、固定定位与阈值、sort_keys 写盘），同一输入结果逐字节一致（`selftest.py` S5 验证）。

## 7. 脚本（均在 SKILL 根目录下以相对路径运行）

- `scripts/main.py`：`skeleton` 立即落盘骨架 / `validate` 校验结构 / `self-test` 委托 selftest。用法见 §4 与 `--help`。
- `scripts/litqa.py`：`locators` 段落定位 / `verify` 逐字引文核验 / `absorb` 确定性合并 / `grade` 阈值聚合分级。
- `scripts/selftest.py`：S1 空目录、S2 坏 JSON、S3 缺输入、S4 正常案例、S5 确定性、S6 证据闭环、S7 结构自检，全部退出码 0 即通过。

自检一键运行：`python3 -B scripts/selftest.py` 或 `python3 -B scripts/main.py self-test`。
