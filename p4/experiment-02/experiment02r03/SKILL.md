---
name: deterministic-feasibility-audit
description: 科研实验方案可行性审查 Skill（确定性审计版）。把研究目标转成可检验问题，对假设与变量、对照组、设备材料、时间与样本、参数范围、风险与异常、伦理与安全七类约束逐条做资源守恒审计，用脚本完成时间/预算/样本/设备机时/货期的真实算术，产出证据可溯源、结论可执行、同输入两次运行逐字节一致的 result.json、可编辑审查报告与 CSV 检查台账。用于对给定科研材料中的候选实验方案做可行性评估、比较与优化时调用。
version: 1.1.0
---

# 实验方案可行性审计（Deterministic Feasibility Audit）

## 1. 角色与何时使用

本 Skill 让 Agent 对一份或多份**候选实验方案**做"能否开工"的审计。核心不是叙事式点评，而是**资源守恒审计**：设备/材料是否到位、时间与样本是否匹配、参数范围是否有据、风险与异常如何处置、伦理与安全是否满足审批路径，每一项都用材料原文与算式说话。

**何时使用**：给定研究目标、约束与科研材料，需要生成、比较、优化或解释实验方案，并明确假设、变量、对照、步骤、参数、资源、风险与异常处理时。**不适用**：实验执行、数据分析、文献综述、论文写作；也不用于无任何真实材料时的"编造方案"。

## 2. 输入契约

运行时输入目录 `/input`，至少包含：

- `/input/case.json`：案例主输入。可能含 `case_id`/`id`/`challenge_id`、研究目标、`plans`（1–3 版候选方案）、资源/价格/预约合同、参数域、样本与伦理信息，以及可选的 `output_contract`。
- `/input/task.md`：题目说明与评分口径。
- 其余材料按实际提供：`materials/`、`sources/`、`notes.md`、PDF 等。**读实际文件，不要凭文件名/领域标签假设内容**；PDF 用平台解析工具取文本，不得假设已有答案层。

工作目录 `/app/work`，最终产物写入 `/app/output/`（含 `result.json`）。

**发现而非假设**：用 `scripts/main.py` 扫描输入目录，容错处理 BOM、坏编码、坏 JSON、超限文件；不假设固定键名。若 `case.json` 含 `output_contract`，其全部字段名必须**原样回填**进结果（缺值给 `null`/`[]`，不更名、不删键）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少含：

- 题目 `output_contract` 的全部字段 + `case_id`（缺省为自动识别值或 `unknown`）。
- `status`：`ok | partial | insufficient_evidence | input_error`。
- `overall_conclusion`：`verdict`（`feasible | conditionally_feasible | not_feasible | undetermined`）、`summary`、`recommended_plan`、`blocking_issues`、`conditions`、`unblock_actions`。
- `experiment_plan`：`research_question`、`hypothesis`、`assumptions`、`variables`、`controls`、`steps`、`parameters`、`resources`、`sample_plan`、`interpretation_criteria`。
- `review`：七类约束 `assumptions_variables`、`controls`、`equipment_materials`、`time_cost`、`parameter_ranges`、`risks_exceptions`、`ethics_safety`，每类为审查项数组。
- `check_ledger`、`plan_comparison`、`version_diff`、`contradictions`、`evidence_and_risks`、`confirmation`、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`、`artifacts`、`provenance`。

审查项（`review.*` 元素）结构：

```json
{
  "id": "EM-03@v2", "dimension": "equipment_materials", "claim": "……",
  "verdict": "conditional", "plan": "v2", "resource_state": "需采购",
  "conditions": ["采购前置期 21 天；下单前确认货期"],
  "evidence": [{"locator": "$.plans[1].materials[0]", "quote": "逐字原文", "kind": "plan_resource", "verified": true}],
  "formula": "50 µg / 50 µg = 1 pack", "computed": 800.0, "unit": "CNY",
  "confidence": "high", "needs_expert": false, "blocking": false, "notes": []
}
```

- `verdict` 枚举：`ok | conditional | blocked | unconfirmed | missing | conflict`（含义见 `references/review_rubric.md`）。
- `resource_state` 仅资源维度：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。**未命中清单 ≠ 不具备**，清单不完整时如实标 `未知`。
- `ok/conditional/blocked/conflict` 必须带 `evidence`（`locator + quote`）；`unconfirmed/missing` 用 `conditions` 说明缺什么。

另写可编辑产物：`review_report.md`（同内容逐条证据定位）、`check_ledger.csv`（Excel 可打开）、`audit_digest.md`（待人工确认项摘要）、`run_manifest.json`（输入/结果 SHA-256）。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进。** 每步结束都重新保存 `result.json`（写盘失败则降级到最小合法产物，进程不得崩溃）。

| 步 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| S0 启动骨架 | 0–1 分钟 | ≤2 | `/input` → `python3 scripts/main.py --input /input --output /app/output --work /app/work`，产出合法 `result.json`（确定性内核 + 算术） |
| S1 发现与阅读 | 1–3 分钟 | ≤12 | 读 `case.json`/`task.md`/材料清单 → 定位研究目标、候选方案、资源合同、参数域、样本与伦理信息；读 `audit_digest.md` 找待确认项 |
| S2 语义补强 | 3–8 分钟 | ≤20 | 对 `unconfirmed`/`needs_expert`/矛盾项读原文片段，写 `work/findings.json` |
| S3 合并与比较 | 8–12 分钟 | ≤12 | `python3 scripts/main.py … --findings work/findings.json` 合并人工语义判定（逐条校验枚举与证据） |
| S4 结论与确认 | 12–14 分钟 | ≤8 | 各维度判断 → `overall_conclusion`、`confirmation.*`、`uncertainty`、`pending_human` |
| S5 自检收尾 | 14–15 分钟 | ≤2 | `python3 scripts/selftest.py --input /input --output /app/output` → 修正 FAIL → 补 `change_log`、终版落盘 |

S0 后立即判定材料是否足够：空目录/缺 `case.json` → `insufficient_evidence` 并列出缺口；`case.json` 无法解析 → `input_error`；否则继续、状态保持 `partial` 直到 S5 收口为 `ok` 或保留 `partial`。

**Agent 人工修订格式**（`work/findings.json`）：`{"review": {"<dimension>": [<审查项对象>]}}`。每个 firm 结论必须带 `evidence[].locator + quote`；脚本会校验枚举与证据，非法项记入 `errors` 不合并。

## 5. 证据溯源（硬要求）

- 定位格式：`$.plans[0].steps[2]`、`$.contracts.equipment[0]`、`materials/<name>.md#<行>`、`sources/<name>.pdf#p<页>`、`task.md#<行>`。
- `evidence.quote` 必须是材料中的逐字原文（脚本用"字符串叶节点/值字面量/文本行"三路取 quote，并由 `selftest.py` 二次核对是否真在来源出现）。
- 冲突信息不隐藏：同一指标两个来源不一致 → 两条都记录并标 `verdict="conflict"` 或 `contradictions`。
- 不虚构引用、DOI、数值、价格、审批状态；缺失写 `pending_human` 或 `missing`。

## 6. 审查口径要点（详见 `references/review_rubric.md`）

1. **"方案没写" ≠ "方案满足"**：未写即 `missing`；未命中清单 ≠ "不具备"，标 `未知` 并 `needs_expert`。
2. **时间/参数/样本/成本必须算出来**：时长累加、设备机时占比、预算差额、样本消耗 `per_group×groups×replicates`、货期松弛，全部给算式与依据，不用"感觉一个数"。
3. **结论可执行**：`conditional` 写清条件；`blocked` 写清缺什么；`unconfirmed` 明确"需谁、确认什么、约多久"。

## 7. 自检清单（交付前逐条勾选，对应评分维度）

对照评分维度，用 `python3 scripts/selftest.py --input /input --output /app/output` 自动校验，并逐条人工确认：

**实际效果**

- [ ] 七类约束各至少一条证据化判断，无"未写当满足"
- [ ] 每条 `ok/conditional/blocked/conflict` 都有 `locator + quote`，且 `verified=true`
- [ ] 数值结论有算式与依据（时长累加、预算、样本消耗、设备机时、货期）
- [ ] 指出方案缺失信息与自相矛盾处（`missing` / `contradictions`）

**使用增益 / 创新增益**

- [ ] 相同方案相同口径可复跑：判断依赖材料原文而非记忆
- [ ] 时间/参数/样本校验用脚本函数而非口头估计
- [ ] 三版方案有 `plan_comparison` 排序与 `version_diff`

**可靠体验**

- [ ] `result.json` 合法且含 `case_id`、`status`、契约字段
- [ ] 空输入/坏 JSON/缺页/超长文档/伦理未定/方案矛盾均有明确处置
- [ ] 同输入两次运行 `result_sha256` 一致（`--determinism` 门禁通过）

**生态贡献**

- [ ] `SKILL.md` 写清用途、输入输出、失败处理；脚本与参考文件真实存在、相对路径可执行
- [ ] `review_report.md`/`check_ledger.csv` 可编辑复用，检查项体系可迁移

## 8. 降级策略（缺数据/缺软件时如实标注，不编造）

- 材料不足 → `status="insufficient_evidence"`，在 `uncertainty`/`pending_human` 列出缺口，**不编造**方案、数值、结论。
- 坏 JSON/坏编码/超限文件 → 记入 `errors`/`degradations`，继续处理其余材料。
- 缺软件（如 PDF 解析不可用）→ 记录到 `degradations`，标注"该证据未能读取"，不得假装已读。
- 伦理/安全/设备可用性未经审批或无法确认 → `unconfirmed` + `needs_expert=true` + `blocking=true`，绝不写成可执行事实。
- 写盘失败 → 降级到最小合法 JSON，进程退出码 0。

## 9. 脚本与调用

在 Skill 根目录（含本文件与 `scripts/`）下，用相对路径调用；仅依赖 Python 标准库（≥3.8）：

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work
python3 scripts/main.py --input /input --output /app/output --findings work/findings.json
python3 scripts/selftest.py --input /input --output /app/output
python3 scripts/selftest.py --input /input --determinism
```

若相对路径不可解析，回退绝对路径 `/skills/weekly-skill/scripts/<name>.py`；不影响结果字段。

## 10. 边界

一个 Skill 只解决"方案可行性审查"一类任务；无真实材料时输出"材料不足"而非示例填充；不预写正式答案；不冒充评测分数或伪造运行结果；运行时墙钟/请求数由平台记录，结果只写预算，不写未测量的耗时。
