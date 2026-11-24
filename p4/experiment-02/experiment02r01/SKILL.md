---
name: versioned-feasibility-review
description: 版本化科研实验方案可行性审查 Skill。把研究目标转为可检验问题，对假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常、伦理与安全七类约束逐条审查，并对步骤时长、成本、样本量与参数域做确定性算术校验；每个结论强制附带 locator 与逐字 quote，经脚本回查原文后产出可复核的 result.json、evidence_map.json 与可编辑 review_report.md。用于对给定科研材料中的多版本候选方案做可行性评估、比较与优化时调用。
version: 2.0.0
---

# 版本化实验方案可行性审查

## 1. 角色与何时使用

本 Skill 让 Agent 对一份或多份（通常 3 版）候选实验方案做“可开工性”审查：把研究目标转为可检验问题与变量，设计/核对步骤、对照、参数、资源与判读标准，并逐条核对设备与材料是否到位、时间与样本是否匹配、参数范围是否有据、风险与异常如何处置、伦理与安全审批能否通过。判定必须落到方案的具体章节、参数与清单行；结论必须可执行、可复核。

**适用**：给定研究目标、约束与真实材料，生成/比较/优化/解释实验方案并做可行性审查。**不适用**：实验执行、数据分析、文献综述、论文写作；无真实材料时不编造方案。

## 2. 输入契约

运行时输入目录 `/input`，至少含：

- `/input/case.json`（或 `case.jsonl`）：案例主输入，可能含 `case_id`、研究目标、`plans`（1–3 版）、`contracts`（设备/材料/预约/报价）、`parameter_domains`、`samples`、`ethics` 及可选 `output_contract`。
- `/input/task.md`：题目说明与评分口径。
- 其余材料按实际提供：`materials/`、`sources/`、PDF 等。**读实际文件，不凭文件名/领域标签假设内容**；PDF 用 `pdftotext`（或 pypdf）取文本，不得假设已有答案层。

工作目录 `/app/work`，最终产物写 `/app/output/`。**先发现后假设**：`main.py init` 扫描输入并容错 BOM、坏编码、坏 JSON、超 2MB 文件；不假设固定键名。若 `case.json` 含 `output_contract`，其全部字段名必须**原样回填**进 `result.json`（缺值给 `null`/`[]`，不更名、不删键）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少含：

- 题目 `output_contract` 全部字段 + `case_id`（缺省自动识别或 `unknown`）。
- `status`：`ok | partial | insufficient_evidence | input_error`。
- `experiment_plan`：`research_question`、`assumptions`、`variables`、`controls`、`steps`、`parameters`、`resources`、`interpretation_criteria`。
- `review`：七类约束，每类为审查项数组；另有 `findings_index`、`plan_comparison`（列表）与 `plan_comparison_meta`。
- `evidence_and_risks`、`confirmation`、`arithmetic_ledger`、`evidence_summary`。
- `sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`limitations`、`contract_binding`、`change_log`、`runtime`、`artifacts`。

审查项结构（写入 `findings.jsonl`，一行一对象）：

```json
{"finding_id":"F-EQ-1","dimension":"equipment_materials","claim":"……",
 "verdict":"conditional","resource_state":"需采购","check_basis":"contract",
 "applies_to":["v1"],"conditions":["……"],"evidence":[{"locator":"case.json#/contracts/equipment/washer/model",
 "quote":"逐字原文","role":"supports"}],
 "computation":{"id":"A-C-1","kind":"cost_total","formula":"c0 + c1","operands":{"c0":100,"c1":50},"result":150,"unit":"CNY"},
 "action":{"owner":"","by":"","acceptance":"","ask":""},"confidence":"high","needs_expert":false,"notes":[]}
```

- `verdict`：`ok | conditional | blocked | unconfirmed | missing | conflict`；`conditional` 必须写 `conditions`，`blocked` 写 `blockers`，`unconfirmed` 写 `needs_expert` 或 `action`。
- `resource_state` 仅资源维度：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。
- `evidence`：每条必须 `locator + quote`；`role`：`supports | contradicts | defines | context`。
- `check_basis`：`plan_stated | plan_silent | contract | domain | computed | source`。

另写可编辑 `/app/output/review_report.md`（与 result.json 同内容、逐条定位）与 `/app/output/evidence_map.json`（locator ↔ quote ↔ 审查项双向映射）。

## 4. 执行流程与预算

总预算 **900 秒 / 64 次模型请求**。**硬规则：前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进。** 每步结束重写 `result.json`（写盘失败降级到最小合法 JSON，进程退出码 0）。

| 步 | 时间 | 请求 | 动作 |
|---|---|---|---|
| S0 启动骨架 | 0–1 min | ≤4 | `python3 scripts/main.py init --input /input --output /app/output --work /app/work` → partial `result.json` + `work/casepack.json` |
| S1 发现与阅读 | 1–3 min | ≤12 | 读 casepack 的 `plans`/`contracts`/`domains`/`facts`/`gaps`/`silence_flags`，用 `main.py show/grep` 定点取原文 |
| S2 七类审查 | 3–9 min | ≤24 | 每类 2–4 条证据化判断，追加写入 `work/findings.jsonl`（先写再改） |
| S3 算术与比较 | 9–12 min | ≤12 | 时长/成本/样本量/参数域用 `assemble.py` 复算；写 `comparison.json`/`contract_values.json` |
| S4 结论与确认 | 12–14 min | ≤8 | `conclusion.json`、`extras.json`（uncertainty/pending_human/limitations）、`plan_notes.json` |
| S5 自检收尾 | 14–15 min | ≤4 | `python3 scripts/assemble.py ...` → `python3 scripts/selfcheck.py ...`，修 FAIL，终版落盘 |

S0 后立即判定：无任何可读输入 → `insufficient_evidence` 并列出缺口；`case.json` 无法解析 → `input_error`；否则继续，状态保持 `partial` 直到 S5 收口。

## 5. 审查口径（七类约束）

核心三条：**“方案没写”≠“方案满足”**（未写记 `missing`）；**时间/参数/样本量必须算出来**（用脚本复算，不凭感觉）；**结论可执行**（条件、缺什么、需谁确认、多久、验收标准写清）。

- `assumptions_variables`：研究问题可检验性、自变量/因变量/混杂、假设是否有据。
- `controls`：阴性/阳性/空白/质控与批次对照；无对照记 `missing`。
- `equipment_materials`：型号/精度/附件/耗材与清单比对，落 `resource_state`。
- `time_cost`：设备占用、人工、等待、检测周转分别估算并累加；核对 deadline 与预约。
- `parameter_ranges`：每个参数对 `min/max/step` 域判定 `min <= value <= max`。
- `risks_exceptions`：设备故障、批次差异、样本不足、参数越界的降级路径与转人工节点。
- `ethics_safety`：动物/人体样本/危险试剂/个人数据 → 审批层级、材料与耗时；未定记 `unconfirmed`。

判定标准、枚举与算术规则详见 `references/review_rubric.md`。

## 6. 证据溯源与算术校验（硬要求）

- 定位：`case.json#/plans/0/steps/1`（JSON Pointer）、`task.md#L9-L27`、`task.md#H:评分维度概要`、`sources/x.pdf#p3`、`materials/x.md#L4`。
- `evidence.quote` 必须是材料**逐字原文**（空白可归一化，≥3 字或纯数字），过长截取决定性子句；不转写、不补全。
- 冲突不隐藏：同指标两来源不一致，两条都记录并标 `verdict="conflict"` 或 `role="contradicts"`。
- 不虚构引用、DOI、数值、价格、审批状态；缺失写 `pending_human`/`missing`。
- 计算用 `computation`（`formula + operands + result + unit`），允许 `+ - * / ** %`、`sum/min/max/abs/round/ceil/floor/sqrt` 与链式比较；`assemble.py`/`selfcheck.py` 独立复算，不符即 FAIL。
- 取原文快捷键：`python3 scripts/main.py show --locator "…"`、`python3 scripts/main.py grep --pattern "4 °C"`。

## 7. 自检清单（交付前逐条勾选，对应 selfcheck.py 代码）

**实际效果**

- [ ] 七类约束各至少 1 条（建议 ≥2）审查项，无“未写当满足”（C05/C21）
- [ ] 每条 `ok/conditional/blocked/conflict` 都有 `locator + quote`，且 quote 通过原文回查（C08）
- [ ] 数值结论有 `computation` 且复算一致（C10/C11）
- [ ] 指出方案缺失信息与自相矛盾处（C09/C19）

**使用增益 / 创新增益**

- [ ] 同方案同口径可复跑：判断依赖材料原文，非记忆（C08 回查强制）
- [ ] 时间/参数/样本量用脚本函数，非口头估计（C10）
- [ ] 版本差异由脚本对齐（`plan_diff`），比较不是空话（C19）

**可靠体验**

- [ ] `result.json` 合法，含 `case_id`、`status` 与契约字段（C01–C04）
- [ ] 空输入/坏 JSON/缺页/超长文档/伦理未定/方案矛盾均有处置（C15/C16）
- [ ] `status` 与自检结果一致，不虚报 ok（C18）

**生态贡献**

- [ ] `SKILL.md` 写清用途、输入输出、失败处理；脚本真实存在、相对路径可执行（C20）
- [ ] `review_report.md` 可编辑；`evidence_map.json` 可复用（C13/C14）

## 8. 降级策略（如实标注 limitations，不编造）

- 材料不足 → `status="insufficient_evidence"`，在 `uncertainty`/`pending_human` 列缺口，不编方案/数值/结论。
- 坏 JSON/坏编码/超限文件 → 记入 `errors`/`degradations`，继续处理其余材料。
- PDF 解析不可用 → `degradations` 标注“该证据未能读取”，不得假装已读。
- 伦理/安全/设备可用性未经审批 → `unconfirmed` + `needs_expert=true`，绝不写成事实。
- quote 未通过回查 → `evidence.verified=false` 并计入 `errors`，用 `main.py show` 取回原文后修正。

## 9. 脚本与调用（相对路径，标准库）

在 Skill 根目录运行：

```bash
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/main.py show --locator "case.json#/plans/0/steps/1"
python3 scripts/main.py grep --pattern "4 °C"
python3 scripts/assemble.py --input /input --output /app/output --work /app/work
python3 scripts/selfcheck.py --input /input --output /app/output --work /app/work --strict
```

`assemble.py` 读取 `work/findings.jsonl`（必需）及可选 `contract_values.json`、`conclusion.json`、`comparison.json`、`plan_notes.json`、`extras.json`。若相对路径不可解析，回退绝对路径 `/skills/weekly-skill/scripts/<name>.py`。仅依赖 Python ≥3.8 标准库。

## 10. 边界

一个 Skill 只解决“多版本实验方案可行性审查”一类任务；无真实材料时输出“材料不足”，不示例填充；不预写正式答案；不冒充评测分数或伪造运行结果；PDF/原论文与实际输入是唯一事实依据，不按领域标签补造数据。
