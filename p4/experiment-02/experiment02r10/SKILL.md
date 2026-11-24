---
name: experiment-feasibility-review
description: 科研实验方案可行性审查 Skill：先把 /input 原始材料归一为可审计的中间表示（case_record），再对假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常、伦理与安全七类约束逐条做证据化判定与确定性算术校验，产出可复核的 result.json 与可编辑审查报告。用于依据给定研究目标、约束与材料生成、比较、优化或解释实验方案，并明确变量、对照、参数、风险与异常处理时调用。
version: 1.1.0
---

# 实验方案可行性审查（Experiment Feasibility Review）

## 1. 角色与何时使用

本 Skill 让 Agent 对一份或多份**候选实验方案**做"可开工性"审查：既评技术合理性，也逐条核对设备/材料是否到位、时间与样本是否匹配、参数范围是否有据、风险与异常如何处置、伦理与安全是否满足审批路径。判定必须落到方案的具体章节、参数与清单行；结论必须可执行、可复核。

**何时使用**：给定研究目标、约束与科研材料，需要生成、比较、优化或解释实验方案并明确假设、变量、对照、步骤、参数、资源、风险与异常处理时。**不适用**：实验执行、数据分析、文献综述、论文写作；无任何真实材料时不得编造方案。

## 2. 架构（IR-first + 证据账本 + 分层门校验）

与"边读边写"的单体脚本不同，本 Skill 采用三段式：

1. **IR-first 中间表示**：`scripts/main.py` 先把 /input 归一为 `/app/work/case_record.json`（研究目标、候选方案、资源/价格合同、参数域、样本、伦理与安全、材料清单），后续审查与算术都作用于该 IR，原始输入只读不改。
2. **统一证据账本**：每条关键结论的 `locator + 逐字 quote` 写入一个去重账本（`evidence` 数组），同一定位只存一次，供结果复用与复核。
3. **分层门校验**：`scripts/selfcheck.py` 按 G1 契约回填 → G2 结构 → G3 证据溯源 → G4 算术 → G5 七类覆盖 → G6 状态一致 的顺序逐层校验，先验"契约是否回填完整"再验"结论是否有据"，失败项逐条修正后才收口。

## 3. 输入契约

运行时输入目录为 `/input`，至少包含：

- `/input/case.json`：案例主输入。可能含 `case_id`/`id`/`challenge_id`/`armiId`、`title`、研究目标、`plans`（1–3 版候选方案）、资源/价格/预约合同、参数域、样本与伦理信息、可选的 `output_contract`。
- `/input/task.md`：题目说明与评分口径。
- 其余按实际提供：`materials/`、`sources/`、`notes.md`、PDF 等。**读实际文件，不凭文件名/领域标签假设内容**；PDF 用平台解析工具取文本，不假设已有答案层。

工作目录 `/app/work`，最终产物写入 `/app/output/`（含 `result.json`）。

**发现而非假设**：用 `scripts/main.py` 扫描输入目录，容错处理 BOM、坏编码、坏 JSON、超 2MB 文件；不假设固定键名。若 `case.json` 含 `output_contract`，其全部字段名必须**原样回填**进结果（缺值给 `null`/`[]`，不更名、不删键、不改类型）。

## 4. 输出契约

必须产出 `/app/output/result.json`，至少含：

- 题目 `output_contract` 的全部字段（原样回填）+ `case_id`（缺省为自动识别值或 `unknown`）。
- `status`：`ok | partial | insufficient_evidence | input_error`。
- `experiment_plan`：`research_question`、`assumptions`、`variables`、`controls`、`steps`、`parameters`、`resources`、`interpretation_criteria`。
- `review`：七类约束 `assumptions_variables`、`controls`、`equipment_materials`、`time_cost`、`parameter_ranges`、`risks_exceptions`、`ethics_safety`，每类为审查项数组。
- `plan_comparison`：多版候选方案比较（有则填）。
- `evidence_and_risks`：`evidence`、`risks`、`exception_handling`。
- `confirmation`：`safety`、`ethics`、`equipment_availability`、`expert_confirmation`。
- `sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`、`artifacts`。

审查项（`review.*` 元素）结构：

```json
{
  "id": "eq-1", "dimension": "equipment_materials", "claim": "……",
  "verdict": "conditional", "resource_state": "需采购",
  "conditions": ["采购前需确认……"], "evidence": [{"locator": "case.json#plan.0.steps[2]", "quote": "逐字原文", "kind": "plan"}],
  "confidence": "medium", "needs_expert": false, "notes": []
}
```

- `verdict` 枚举：`ok | conditional | blocked | unconfirmed | missing | conflict`。
- `resource_state` 仅资源维度：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。
- `conditional` 必须写清 `conditions`（逐条可执行）；`ok/conditional/blocked/conflict` 必须带 `evidence`。

另写一份可编辑的 `/app/output/review_report.md`，与 `result.json` 同内容、逐条列出证据定位，供人工复核。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进。** 每步结束都重新保存 `result.json`（写盘失败则降级到最小合法产物，进程不得崩溃）。

| 步 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| S0 启动骨架 | 0–1 分钟 | ≤4 | `/input` → 运行 `scripts/main.py`，产出合法 partial `result.json` + `/app/work/case_record.json` + `input_inventory.json` |
| S1 发现与阅读 | 1–3 分钟 | ≤12 | 读 `case.json`/`task.md`/材料清单 → 提取研究目标、候选方案、资源合同、参数域、样本与伦理信息 |
| S2 七类审查 | 3–9 分钟 | ≤24 | 材料原文 → 每类 2–4 条证据化判断（S2 起逐步回填 `review.*`） |
| S3 算术与比较 | 9–12 分钟 | ≤12 | 步骤时长/参数范围/样本量/成本 → 用 `scripts/core_impl.py` 的 `sum_time`/`check_range`/`sum_cost`/`check_sample` 做确定性算术校验与方案排序 |
| S4 结论与确认 | 12–14 分钟 | ≤8 | 各维度判断 → `overall_conclusion`、`confirmation.*`、`uncertainty`、`pending_human` |
| S5 自检收尾 | 14–15 分钟 | ≤4 | 运行 `scripts/selfcheck.py` → 修正 FAIL → 补 `change_log`、写 `review_report.md`、终版落盘 |

S0 后立即判定材料是否足够：无任何可读输入 → `insufficient_evidence` 并列出缺口；`case.json` 无法解析 → `input_error`；否则继续审查、状态保持 `partial` 直到 S5 收口为 `ok` 或保留 `partial`。

## 6. 证据溯源（硬要求）

每条关键结论、数值、图元都要能指回**逐字片段**：

- 定位格式：`case.json#plan.<i>.steps[<j>]`、`case.json#contracts.<key>`、`materials/<name>.md#<标题或行>`、`sources/<name>.pdf#p<页>`、`task.md#<行>`。
- `evidence.quote` 必须是材料中的逐字原文；过长时截取决定性子句并给定位，不转写、不补全。
- 冲突信息不隐藏：同一指标两个来源不一致时，两条都记录并标 `verdict="conflict"` 或 `notes` 注明 `source_conflict`。
- 不虚构引用、DOI、数值、价格、审批状态；缺失写 `pending_human` 或 `missing`。

## 7. 审查口径要点

七类约束的判定标准、状态枚举、算术规则详见 `references/feasibility_rubric.md`；降级与诚实标注规则详见 `references/degradation_rules.md`。核心三条：

1. **"方案没写" ≠ "方案满足"**：未写即 `missing`，不得默认满足。
2. **时间/参数/样本量必须算出来**：用 `scripts/core_impl.py` 的确定性函数做真实算术，给出算式与依据，不用"感觉一个数"。
3. **结论可执行**：`conditional` 写清条件；`blocked` 写清缺什么；`unconfirmed` 明确"需谁、确认什么、约多久"。

## 8. 自检清单（交付前逐条勾选）

对照评分维度，用 `python3 scripts/selfcheck.py --input /input --output /app/output` 自动校验；逐条确认：

**实际效果**

- [ ] 七类约束各至少一条证据化判断，且无"未写当满足"
- [ ] 每条 `ok/conditional/blocked/conflict` 都有 `locator + quote`
- [ ] 数值结论有算式与依据（时间累加、参数范围、样本量）
- [ ] 指出方案本身缺失的信息与自相矛盾处

**使用增益 / 创新增益**

- [ ] 相同方案相同口径可复跑：判断依赖材料原文而非记忆
- [ ] 参数/时间校验使用脚本函数而非口头估计

**可靠体验**

- [ ] `result.json` 合法且含 `case_id`、`status`、契约字段
- [ ] 空输入/坏 JSON/缺页/超长文档/伦理未定/方案矛盾均有明确处置
- [ ] 重复运行结构一致（脚本确定性）

**生态贡献**

- [ ] `SKILL.md` 写清用途、输入输出、失败处理；脚本与参考文件真实存在、相对路径可执行
- [ ] `review_report.md` 可编辑，模板与检查项可复用

## 9. 降级策略（缺数据/缺软件时如实标注）

- 材料不足 → `status="insufficient_evidence"`，在 `uncertainty`/`pending_human` 列出缺口，**不编造**方案、数值、结论。
- 坏 JSON/坏编码/超限文件 → 记入 `errors`/`degradations`，继续处理其余材料。
- 缺软件（如 PDF 解析不可用）→ 记录到 `degradations`，标注"该证据未能读取"，不得假装已读。
- 伦理/安全/设备可用性未经审批或无法确认 → `unconfirmed` + `needs_expert=true`，绝不写成可执行事实。
- 写盘失败 → 降级到最小合法 JSON，进程退出码 0。

## 10. 脚本与调用

在 Skill 根目录（含本文件与 `scripts/`）下，用相对路径调用；仅依赖 Python 标准库（≥3.8）：

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work
python3 scripts/core_impl.py --selftest          # 打印算术函数与 IR 的确定性自检
python3 scripts/selfcheck.py --input /input --output /app/output --work /app/work
```

若相对路径不可解析，回退到绝对路径 `/skills/weekly-skill/scripts/<name>.py`；不影响结果字段。

## 11. 最小调用示例（示意，非正式案例）

以下为**接口示意**，不是正式案例结果：

```text
/input/case.json:
  {"case_id":"demo","title":"示意","output_contract":{"research_question":"string","review_summary":"string"},
   "plans":[{"version":"v1","steps":[{"label":"准备","duration":"30 min"}]}]}
```

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work
# → {"status":"partial","case_id":"demo","input_files":2,"contract_keys":2}

python3 scripts/selfcheck.py --input /input --output /app/output --work /app/work
# → G1/G2/G3/G4/G6 PASS，G5 提示 review 为空（S1–S5 回填后应 PASS）
```

`/app/output/result.json` 会含 `case_id`、`status`、`output_contract` 全字段，以及七类 `review` 空数组待回填；`/app/work/case_record.json` 为中间表示，`input_inventory.json` 为输入清单。

## 12. 边界

一个 Skill 只解决"方案可行性审查"一类任务；无真实材料时输出"材料不足"而非示例填充；不预写正式答案；不冒充评测分数或伪造运行结果。
