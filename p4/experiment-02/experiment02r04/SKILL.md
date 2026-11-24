---
name: experiment-feasibility-review
description: 科研实验方案可行性审查 Skill：把研究目标转成可检验问题，对假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常、伦理与安全七类约束逐条做证据化审查与确定性算术校验，把限制与不确定性显式登记为 limitations/uncertainty，缺数据不编造，产出可溯源、可复核的 result.json 与可编辑复核报告。用于对给定科研材料中的候选实验方案做可行性评估、比较与优化时调用。
version: 2.0.0
---

# 实验方案可行性审查（降级与不确定性强化版）

## 1. 角色与何时使用

本 Skill 让 Agent 对候选实验方案做“可开工性”审查：不只评技术合理性，还核对设备/
材料是否到位、时间与样本是否匹配、参数范围是否有据、风险与异常如何处置、伦理与安全
审批路径。判定必须落到方案的具体章节、参数和清单行，结论必须可执行、可复核。

**何时使用**：给定研究目标、约束与科研材料，需要生成、比较、优化或解释实验方案并
明确假设、变量、对照、步骤、参数、资源、风险与异常处理时。**不适用**：实验执行、
数据分析、文献综述、论文写作；也不用于无真实材料时的“编造方案”。

## 2. 输入契约

运行时输入目录 `/input`，至少含 `/input/case.json` 与 `/input/task.md`，其余材料按
实际提供（`materials/`、`sources/`、`notes.md`、PDF 等）。**读实际文件，不凭文件名/
领域标签假设内容**；PDF 用平台解析工具取文本，不假设已有答案层。

工作目录 `/app/work`，最终产物写入 `/app/output/`（含 `result.json`）。**发现而非
假设**：用 `scripts/main.py` 扫描输入目录，容错处理 BOM、坏编码、坏 JSON、超 2MB 文件。
若 `case.json` 含 `output_contract`，其全部字段名必须**原样回填**进结果顶层（缺值给
`null`/`[]`，不更名、不删键）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少含：

- 题目 `output_contract` 的全部字段（原样回填）+ `case_id`（缺省为自动识别值或 `unknown`）。
- `status`：`ok | partial | insufficient_evidence | input_error`。
- `experiment_plan`：`research_question`、`assumptions`、`variables`、`controls`、`steps`、
  `parameters`、`resources`、`interpretation_criteria`。
- `review`：七类约束 `assumptions_variables`、`controls`、`equipment_materials`、`time_cost`、
  `parameter_ranges`、`risks_exceptions`、`ethics_safety`，每类为审查项数组。
- `plan_comparison`、`evidence_and_risks`、`confirmation`（safety/ethics/equipment_availability/
  expert_confirmation）。
- **本版强化** `limitations`（六类限制登记）、`uncertainty`、`pending_human`、`sources`、
  `errors`、`degradations`、`change_log`、`runtime`、`artifacts`、`contract`。

审查项结构（`review.*` 元素）：

```json
{"id": "eq-1", "dimension": "equipment_materials", "claim": "……",
 "verdict": "conditional", "resource_state": "需采购", "conditions": ["采购前需确认……"],
 "evidence": [{"locator": "case.json#plan.0.steps[2]", "quote": "逐字原文", "kind": "observed"}],
 "confidence": "medium", "needs_expert": false, "notes": []}
```

- `verdict` 枚举：`ok | conditional | blocked | unconfirmed | missing | conflict`。
- `resource_state` 仅资源维度：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。
- `conditional` 必须写清可执行 `conditions`；`ok/conditional/blocked/conflict` 必须带 `evidence`。

另写一份可编辑 `/app/output/review_report.md`，与 `result.json` 同内容、逐条列出证据
定位，供人工复核。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 16384 tokens）

**硬规则：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进。** 每步结束都重新
保存 `result.json`（写盘失败则降级到最小合法产物，进程不得崩溃）。

| 步 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| S0 启动骨架 | 0–1 分钟 | ≤4 | `/input` → `scripts/main.py`，产出 partial `result.json` + `input_inventory.json` |
| S1 发现与阅读 | 1–3 分钟 | ≤12 | 读 `case.json`/`task.md`/材料 → 提取研究目标、候选方案、资源合同、参数域、样本与伦理信息 |
| S2 七类审查 | 3–9 分钟 | ≤24 | 材料原文 → 每类 2–4 条证据化判断，每步同步登记 `limitations.*` |
| S3 算术与比较 | 9–12 分钟 | ≤12 | 时长/参数/样本量/成本 → `review.py` 确定性校验与方案排序，登记 `arithmetic_uncertainty` |
| S4 结论与确认 | 12–14 分钟 | ≤8 | 各维度判断 → `overall_conclusion`、`confirmation.*`、`uncertainty`、`pending_human` |
| S5 自检收尾 | 14–15 分钟 | ≤4 | `scripts/selftest.py` → 修 FAIL → 补 `change_log`、写 `review_report.md`、终版落盘 |

S0 后立即判定材料是否足够：无任何可读输入 → `insufficient_evidence` 并列出缺口；
`case.json` 无法解析 → `input_error`；否则继续审查，状态保持 `partial` 直到 S5 收口为
`ok` 或保留 `partial`（保留时 `limitations`/`uncertainty` 必须非空）。

## 5. 证据溯源与不编造

每条关键结论、数值、图元都要能指回**逐字片段**：`evidence.locator` + `evidence.quote`
（逐字原文，过长截决定性子句，不转写不补全）。每个数值结论必须落证据四态之一：
`observed`（引用原文）/ `derived`（`formula` 算式）/ `assumed`（显式假设）/
`unreported`（记为 `missing`）。无 `locator`、无 `formula`、无假设的数值视为疑似编造，
进入 `unverifiable_claims`。冲突信息不隐藏，两条都记录并标 `source_conflict`。

## 6. 降级与不确定性（本版重点）

- 材料不足 → `status="insufficient_evidence"`，在 `limitations`/`uncertainty`/`pending_human`
  列出缺口，**不编造**方案、数值、结论。
- 坏 JSON/坏编码/超限 → 记入 `errors`/`degradations`，继续处理其余材料。
- 缺软件（如 PDF 解析不可用）→ 记入 `degradations`，标“该证据未能读取”，不假装已读。
- 伦理/安全/设备可用性未经审批 → `unconfirmed` + `needs_expert=true`，绝不写成可执行事实。
- 写盘失败 → 降级到最小合法 JSON，进程退出码 0。
- 未写 ≠ 满足：未写即 `missing`；未确认即 `unconfirmed`。
- 细节口径见 `references/feasibility_rubric.md` 与 `references/limitations_and_uncertainty.md`。

## 7. 自检清单（交付前逐条勾选）

**实际效果**
- [ ] 七类约束各至少一条证据化判断，无“未写当满足”
- [ ] 每条 `ok/conditional/blocked/conflict` 都有 `locator + 逐字 quote`
- [ ] 数值结论有算式与依据（`sum_time`/`check_range`/`check_sample_size`）
- [ ] 指出方案本身缺失的信息与自相矛盾处

**使用增益 / 创新增益**
- [ ] 相同方案相同口径可复跑：判断依赖材料原文而非记忆
- [ ] 参数/时间校验使用 `review.py` 而非口头估计
- [ ] 限制与不确定性显式登记（六类 `limitations`），缺数据不编造

**可靠体验**
- [ ] `result.json` 合法且含 `case_id`、`status`、契约字段
- [ ] 空输入/坏 JSON/缺页/超长文档/伦理未定/方案矛盾均有明确处置
- [ ] 重复运行结构一致（脚本确定性、无随机数、无墙钟时间戳）

**生态贡献**
- [ ] `SKILL.md` 写清用途、输入输出、失败处理；脚本与参考文件真实存在、相对路径可执行
- [ ] `review_report.md` 可编辑，模板与检查项可复用

## 8. 脚本与调用

在 Skill 根目录（含本文件与 `scripts/`）下用相对路径调用；仅依赖 Python 标准库
（≥3.8），`python3` 与 `python` 均可：

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work
python3 scripts/review.py --print-schema          # 打印审查 schema 与状态枚举
python3 scripts/selftest.py --input /input --output /app/output
```

若相对路径不可解析，回退绝对路径 `/skills/weekly-skill/scripts/<name>.py`；不影响结果字段。

## 9. 边界

一个 Skill 只解决“方案可行性审查”一类任务；无真实材料时输出“材料不足”而非示例填充；
不预写正式答案；不冒充评测分数或伪造运行结果；`GITHUB_URL.txt` 仅在确有公开链接时由
提交者在包外提供，本 Skill 不内置。
