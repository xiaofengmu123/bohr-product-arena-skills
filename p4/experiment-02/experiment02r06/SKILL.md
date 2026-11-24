---
name: experiment-feasibility-review
description: 科研实验方案可行性审查 Skill。把版本化候选方案、资源/价格/预约合同、参数域与样本伦理信息规范化为可复算的中间表示 plan-model/1.0，再用确定性校验引擎对七类约束（假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常、伦理与安全）逐条产出带 locator+逐字 quote 的判定，交付 result.json 与可编辑 review_report.md。用于对给定科研材料中的候选实验方案做开工前可行性评估、版本比较与优化时调用。
version: 1.1.0
---

# 实验方案可行性审查（Experiment Feasibility Review）

## 1. 角色与何时使用

本 Skill 审查一份或多份**版本化候选实验方案**是否"可开工"，并把每个结论落到方案的具体章节、参数和清单行。区别于只评技术合理性：本 Skill 强制核对设备/材料是否到位、时间与样本是否匹配、参数是否有据、风险与异常如何处置、伦理与安全是否满足审批路径。

**何时使用**：给定研究目标、约束与真实材料，需要生成、比较、优化或解释实验方案，并明确假设、变量、对照、步骤、参数、资源、风险与异常处理时。**不适用**：实验执行、数据分析、文献综述、论文写作；无任何真实材料时的"编造方案"。

核心方法：先构建**结构化中间表示** `plan_model.json`，再只读该模型做算术与判定。任何派生数值都带公式与输入（`calc_trace.json`），因此同一份材料换人复跑会得到可比较的结论。

## 2. 输入契约

运行时输入目录 `/input`，至少包含：

- `/input/case.json`（或 `case.jsonl`）：案例主输入。可能含 `case_id`/`id`/`challenge_id`/`armiId`、`title`、研究目标、`plans`（1–3 个版本，可为列表或按版本名键控的映射）、资源清单/价格合同/预约合同、参数域、样本与伦理信息、以及可选的 `output_contract`。
- `/input/task.md`：题目与评分口径。
- 其余材料按实际提供（`materials/`、`sources/`、`notes.md`、PDF 等）。**读实际文件，不凭文件名或领域标签假设内容**；PDF 需用平台解析工具取文本，不假设已有答案层。

工作目录 `/app/work`，最终产物写入 `/app/output/`（含 `result.json`）。

**发现而非假设**：脚本按别名识别常见键名（如 `plans/candidates/versions`、`steps/procedure/workflow`），不要求固定结构；BOM、坏编码、坏 JSON、超 2MB 文件均降级记录而不崩溃。若 `case.json` 含 `output_contract`，其全部字段名必须**原样回填**进结果（缺值给 `null`，不更名、不删键）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少含：

- `case_id`、`status`（`ok | partial | insufficient_evidence | input_error`）；
- 题目 `output_contract` 的全部字段（按语义回填到顶层，未匹配字段保留 `null` 并记入 `contract_fill_report`）；
- `overall_conclusion`（verdict/summary/conditions/blocks/recommended_plan/counts）；
- `experiment_plan`：`research_question`、`assumptions`、`variables`、`controls`、`steps`、`parameters`、`resources`、`interpretation_criteria`、`sample_plan`；
- `review`：七类约束 `assumptions_variables`、`controls`、`equipment_materials`、`time_cost`、`parameter_ranges`、`risks_exceptions`、`ethics_safety`，每类为审查项数组；
- `plan_comparison`（ranking/diffs/recommended，多版本时有）；
- `evidence_and_risks`、`confirmation`（safety/ethics/equipment_availability/expert_confirmation）；
- `sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`、`artifacts`、`integrity`（结果指纹）。

审查项结构：`{id, dimension, plan_id, subject, claim, verdict, resource_state, conditions, evidence:[{locator, quote, kind}], calc_refs, confidence, needs_expert, notes}`。`verdict` 枚举与 `resource_state` 枚举见 `references/check_library.md`。

另产出三件可复核产物：

- `/app/output/plan_model.json`：结构化中间表示（schema `plan-model/1.0`），是全部判断的单一事实来源；
- `/app/output/calc_trace.json`：每个派生数值的 `formula/inputs/value/unit/method/locators`；
- `/app/output/review_report.md`：与 `result.json` 同内容、逐条列出证据定位，供人工复核与编辑。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进。** 每步结束都重写 `result.json`；写盘失败则降级到最小合法产物，进程退出码 0。

| 步 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| S0 启动骨架 | 0–1 分钟 | ≤4 | 运行 `scripts/main.py --stage skeleton` → 写 `result.json`（partial）+ `input_inventory.json` |
| S1 发现与阅读 | 1–3 分钟 | ≤12 | 读 `case.json`/`task.md`/材料 → 提取研究目标、版本化方案、资源合同、参数域、样本与伦理信息 |
| S2 建模与七类检查 | 3–9 分钟 | ≤24 | 运行 `scripts/main.py --stage full` → 生成 `plan_model.json`、机械 finding 与 calc |
| S3 人工复核与补证 | 9–12 分钟 | ≤12 | Agent 复核机械判断：补文献依据、判定阈值、专家意见 → 写 `/app/work/adjudication.json` |
| S4 合并与结论 | 12–14 分钟 | ≤8 | `--adjudication ...` 重跑 full → `overall_conclusion`、`confirmation.*`、`uncertainty`、`pending_human` |
| S5 自检收尾 | 14–15 分钟 | ≤4 | `scripts/selftest.py` 对接口自检；核对 `scripts/selfcheck.py` 清单；补 `change_log`、终版落盘 |

S0 后立即判定材料是否足够：无任何可读输入 → `insufficient_evidence` 并列出缺口；`case.json` 无法解析 → `input_error`；否则继续审查，状态保持 `partial`，直到 S5 收口为 `ok` 或保留 `partial`。

## 5. 证据溯源（硬要求）

- 定位格式：`case.json#plans[<i>].steps[<j>]`、`case.json#resources.equipment[<k>]`、`case.json#contracts.<key>`、`task.md#<行>`、`materials/<name>.md#<标题或行>`、`sources/<name>.pdf#p<页>`。
- `evidence.quote` 必须是材料中的逐字原文（关键取值串）；过长截取决定性子句并给定位，不转写、不补全。
- 冲突信息不隐藏：同一指标两处不一致时两条都记录，`verdict="conflict"` 或 notes 标 `source_conflict`。
- 不虚构引用、DOI、数值、价格、审批状态；缺失写 `pending_human` 或 `missing`。

## 6. 审查口径要点

判定标准、状态枚举、算术规则与阈值见 `references/check_library.md`；中间表示与契约回填规则见 `references/model_schema.md`。核心三条：

1. **"方案没写" ≠ "方案满足"**：未写即 `missing`，不得默认满足。
2. **时间/参数/样本量必须算出来**：由 `calc_trace.json` 给出 `Σ duration`、关键路径、机时利用率 `need/(capacity-booked)`、货期窗口、成本合计等，不用"感觉一个数"。
3. **结论可执行**：`conditional` 写清条件；`blocked` 写清缺什么；`unconfirmed` 明确"需谁、确认什么、约多久"。

## 7. 自检清单（交付前逐条勾选）

对照评分维度逐条确认：

**实际效果**

- [ ] 七类约束各至少一条判断，且无"未写当满足"
- [ ] 每条 `ok/conditional/blocked/conflict` 都有 `locator + quote`
- [ ] 数值结论有公式与依据（时间、机时、货期、成本、样本量）
- [ ] 指出方案本身缺失的信息与自相矛盾处

**使用增益 / 创新增益**

- [ ] 相同方案相同口径可复跑：`integrity.payload_sha256_12` 两次一致
- [ ] 数值校验来自 `plan_model.json` + `calc_trace.json`，非口头估计
- [ ] 版本比较给出 ranking 与 `v(i)→v(i+1)` 差异（步骤/参数/问题消解）

**可靠体验**

- [ ] `result.json` 合法且含 `case_id`、`status`、契约字段
- [ ] 空输入/坏 JSON/缺材料/方案矛盾均有明确处置
- [ ] 重复运行结构一致（脚本确定性，无墙钟时间戳）

**生态贡献**

- [ ] `SKILL.md` 写清用途、输入输出、失败处理；脚本与参考文件真实存在、相对路径可执行
- [ ] `review_report.md`、`check_library.md`、`model_schema.md` 可复用

## 8. 降级策略（缺数据/缺软件时如实标注）

- 材料不足 → `status="insufficient_evidence"`，在 `uncertainty`/`pending_human` 列出缺口，不编造方案、数值、结论。
- 坏 JSON/坏编码/超限文件 → 记入 `errors`/`degradations`，继续处理其余材料。
- 缺软件（如 PDF 解析不可用）→ 记录到 `degradations`，标注"该证据未能读取"，不得假装已读。
- 伦理/安全/设备可用性未经审批或无法确认 → `unconfirmed` + `needs_expert=true`，绝不写成可执行事实。
- 写盘失败 → 降级到最小合法 JSON，进程退出码 0。

## 9. 脚本与调用

在 Skill 根目录（含本文件与 `scripts/`）下用相对路径调用；仅依赖 Python 标准库（≥3.8）：

```bash
python3 scripts/main.py --stage skeleton --input /input --output /app/output --work /app/work
python3 scripts/main.py --stage full --input /input --output /app/output --work /app/work \
    [--adjudication /app/work/adjudication.json]
python3 scripts/selftest.py     # 在临时目录生成合成案例做端到端自检（非正式答案）
```

若相对路径不可解析，回退绝对路径 `/skills/weekly-skill/scripts/<name>.py`；不影响结果字段。

Agent 复核接口（`/app/work/adjudication.json`）：

```json
{
  "overrides": [{"finding_id": "v1.TC-4", "verdict": "conditional",
                 "conditions": ["..."], "notes": ["..."]}],
  "additions": [{"id": "AGT-01", "dimension": "ethics_safety", "subject": "...",
                 "claim": "...", "verdict": "ok",
                 "evidence": [{"locator": "case.json#ethics", "quote": "原文", "kind": "approval"}]}]
}
```

新增 finding 若无 `locator+quote` 会自动降级为 `unconfirmed`，防止伪造证据。

## 10. 边界

一个 Skill 只解决"方案可行性审查"一类任务；无真实材料时输出"材料不足"而非示例填充；不预写正式答案；不冒充评测分数或伪造运行结果；规划场景的采购/价格/审批仅为组织方提供的对照信息，不当作真实实验室、供应商报价或安全审批事实。
