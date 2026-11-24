---
name: experiment-feasibility-review
description: 科研实验方案可行性审查 Skill（contract-first）。把研究目标转为可检验问题，按假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常、伦理与安全七类约束逐条审查，并用确定性算术做时间/参数/样本/成本校验；动态解析题目 output_contract 并把全部字段原样回填进 result.json，附带 change_log 与证据定位。用于对给定科研材料中的多版候选实验方案做可行性评估、比较、优化与解释时调用。
version: 2.0.0
---

# 实验方案可行性审查（Contract-First Feasibility Review）

## 1. 角色与何时使用

本 Skill 让 Agent 对一份或多份候选实验方案做"可开工性"审查：不止评技术合理性，还要把方案中的设备/材料/时间/样本/参数/风险/伦理逐条对上清单与合同。判定必须落到方案的具体章节、参数与清单行；结论必须可执行（有条件可行要写清条件）、可复核（每句可指回逐字原文）。

**何时使用**：给定研究目标、约束与科研材料，需要生成、比较、优化或解释实验方案，并明确假设、变量、对照、步骤、参数、资源、风险与异常处理时。**不适用**：实验执行、数据分析、文献综述、论文写作；无任何真实材料时不得编造方案。

## 2. 输入契约

运行时输入目录为 `/input`，至少包含 `/input/case.json` 与 `/input/task.md`，其余材料（`materials/`、`sources/`、`notes.md`、PDF 等）按实际提供。**先发现再假设**：读实际文件，不凭文件名或领域标签补造内容；PDF 用平台解析工具取文本，不得假设已有答案层。工作目录 `/app/work`，最终产物写入 `/app/output/`。

`case.json` 可能含 `output_contract`（输出契约）。这是评分硬约束：**其全部字段名必须原样回填进结果，缺值给 `null`/`[]`/`""`，不更名、不删键**。若缺失，则回退到本任务标准契约（见下）。

## 3. 输出契约（contract-first 机制）

必须产出 `/app/output/result.json`，顶层同时包含三部分：

**(A) 回填的契约字段**——若 `case.json#output_contract` 存在，逐键回填其全部字段（递归构造对象/数组骨架）；否则回填标准契约：

```json
{
  "experiment_plan": {"research_question": "", "assumptions": [], "variables": [],
    "controls": [], "steps": [], "parameters": [], "resources": [], "interpretation_criteria": []},
  "evidence_and_risks": {"evidence": [], "risks": [], "exception_handling": []},
  "confirmation": {"safety": [], "ethics": [], "equipment_availability": [], "expert_confirmation": []}
}
```

**(B) 固定元信息**——`case_id`、`status`（`ok | partial | insufficient_evidence | input_error`）、`task_title`、`challenge_id`、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`、`artifacts`。

**(C) 审查主产物**——`review`（七类约束数组）、`plan_comparison`、`overall_conclusion`、`contract_fidelity`（记录每条契约字段的来源、类型推断与 `echoed|defaulted|schema_built` 状态，证明对齐无改名无删键）。

审查项结构（`review.*` 元素）：

```json
{"id":"eq-1","dimension":"equipment_materials","claim":"……",
 "verdict":"conditional","resource_state":"需采购","conditions":["采购前需确认……"],
 "evidence":[{"locator":"case.json#plan.0.steps[2]","quote":"逐字原文","kind":"plan"}],
 "confidence":"medium","needs_expert":false,"notes":[]}
```

`verdict` 枚举：`ok | conditional | blocked | unconfirmed | missing | conflict`；`resource_state` 仅资源维度用：`已具备 | 可外协 | 需采购 | 不具备 | 未知`；`conditional` 必须写清 `conditions`；`ok/conditional/blocked/conflict` 必须带 `evidence`。另写一份同内容的可编辑 `/app/output/review_report.md` 供人工复核。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须落盘一版合法 partial 结果骨架，之后只做增量改进。** 每步结束都重新写盘 `result.json`（失败则降级到最小合法 JSON，退出码 0，进程不崩溃）。

| 步 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| S0 启动骨架 | 0–1 min | ≤4 | 运行 `scripts/main.py` → 合法 partial `result.json` + `input_inventory.json` |
| S1 发现与阅读 | 1–3 min | ≤12 | 读 `case.json`/`task.md`/材料 → 提取目标、多版方案、合同、参数域、样本与伦理信息 |
| S2 七类审查 | 3–9 min | ≤24 | 材料原文 → 每类 2–4 条证据化判断，逐步回填 `review.*` |
| S3 算术与比较 | 9–12 min | ≤12 | 时长/参数范围/样本量/成本 → 用 `review.py` 做确定性算术与方案排序 |
| S4 结论与确认 | 12–14 min | ≤8 | → `overall_conclusion`、`confirmation.*`、`uncertainty`、`pending_human` |
| S5 自检收尾 | 14–15 min | ≤4 | 运行 `scripts/selftest.py` → 修正 FAIL → 补 `change_log`、写报告、终版落盘 |

S0 后立即判定材料是否足够：无任何可读输入 → `insufficient_evidence` 并列出缺口；`case.json` 无法解析 → `input_error`；否则继续审查，状态保持 `partial` 直到 S5 收口为 `ok` 或保留 `partial`。每次写盘都追加一条 `change_log`（含 `step/action/reason` 与结果内容哈希，证明增量而非覆盖）。

## 5. 证据溯源（硬要求）

每条关键结论、数值、图元都要指回**逐字片段**：`evidence.quote` 必须是材料原文，过长时截取决定性子句并给定位，不转写、不补全。定位格式：`case.json#plan.<i>.steps[<j>]`、`case.json#contracts.<key>`、`materials/<name>.md#<标题或行>`、`sources/<name>.pdf#p<页>`、`task.md#<行>`。同一指标两个来源不一致时两条都记录并标 `verdict="conflict"` 或 `notes` 注明 `source_conflict`。不虚构引用、DOI、数值、价格、审批状态；缺失写 `pending_human` 或 `missing`。

## 6. 审查口径要点

七类约束的判定标准、状态枚举与算术规则详见 `references/feasibility_rubric.md`；降级与诚实标注规则详见 `references/degradation_rules.md`。核心三条：

1. **"方案没写" ≠ "方案满足"**：未写即 `missing`，不得默认满足。
2. **时间/参数/样本量/成本必须算出来**：用 `review.py` 的 `sum_time`/`check_range`/`check_sample_size`/`sum_cost` 做真实算术，给出算式与依据，不靠"感觉一个数"。
3. **结论可执行**：`conditional` 写清条件；`blocked` 写清缺什么；`unconfirmed` 明确"需谁、确认什么、约多久"。

本任务固定案例为 3 个候选方案版本（`plan.0/1/2`）＋显式资源/价格/预约合同＋有限参数域，需真实算术与验证；方案比较必须在同一口径下按"阻断条件数、缺失项数、算术可行性"排序，不靠主观打分。

## 7. 自检清单（交付前逐条勾选，用 `python3 scripts/selftest.py` 自动校验）

**实际效果**

- [ ] 七类约束各至少一条证据化判断，且无"未写当满足"
- [ ] 每条 `ok/conditional/blocked/conflict` 都有 `locator + quote`
- [ ] 数值结论有算式与依据（时间累加、参数范围、样本量、成本）
- [ ] 指出方案本身缺失的信息与自相矛盾处

**使用增益 / 创新增益**

- [ ] 相同方案相同口径可复跑：判断依赖材料原文而非记忆
- [ ] 参数/时间校验使用脚本函数而非口头估计

**可靠体验**

- [ ] `result.json` 合法且含 `case_id`、`status`、全部契约字段
- [ ] 空输入/坏 JSON/缺页/超长文档/伦理未定/方案矛盾均有明确处置
- [ ] 重复运行结构一致（脚本确定性）

**生态贡献**

- [ ] `SKILL.md` 写清用途、输入输出、失败处理；脚本与参考文件真实存在、相对路径可执行
- [ ] `review_report.md` 可编辑，模板与检查项可复用

## 8. 降级策略（缺数据/缺软件时如实标注）

- 材料不足 → `status="insufficient_evidence"`，在 `uncertainty`/`pending_human` 列出缺口，不编造方案、数值、结论。
- 坏 JSON/坏编码/超限文件 → 记入 `errors`/`degradations`，继续处理其余材料。
- 缺软件（如 PDF 解析不可用）→ 记入 `degradations` 并标注"该证据未能读取"，不得假装已读。
- 伦理/安全/设备可用性未经审批或无法确认 → `unconfirmed` + `needs_expert=true`，绝不写成可执行事实。
- 写盘失败 → 降级到最小合法 JSON，进程退出码 0。

## 9. 脚本与调用

在 Skill 根目录（含本文件与 `scripts/`）下用相对路径调用；仅依赖 Python 标准库（≥3.8）：

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work
python3 scripts/review.py --print-schema        # 打印审查 schema 与状态枚举
python3 scripts/review.py --self-test           # 算术与契约函数自测
python3 scripts/selftest.py --input /input --output /app/output
```

若相对路径不可解析，回退绝对路径 `/skills/weekly-skill/scripts/<name>.py`，不影响结果字段。`main.py` 幂等：已存在 `result.json` 时只补齐缺失契约字段并保留已填内容。

## 10. 边界

一个 Skill 只解决"方案可行性审查"一类任务；无真实材料时输出"材料不足"而非示例填充；不预写正式答案、不冒充评测分数或伪造运行结果。
