---
name: versioned-feasibility-review
description: 版本化科研实验方案可行性审查 Skill。把研究目标转成可检验问题与变量，对假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常、伦理与安全七类约束逐条审查，做方案×合同交叉核验、有限参数域校验与可复算算术台账，对多版候选方案排序，产出证据可溯源、结论可执行、可复核的 result.json 与可编辑报告。用于对给定科研材料中的候选实验方案做可行性评估、比较与优化时调用。
version: 1.2.0
---

# 版本化实验方案可行性审查

## 1. 角色与何时使用

本 Skill 对一份或多份**候选实验方案**做"可开工性"审查：不只评技术合理性，还核对设备/材料是否到位、货期与排期是否匹配、时间与样本量是否算得出来、参数是否落在有限参数域内、风险与异常如何处置、伦理与安全是否满足审批路径。判定必须落到方案的具体章节、参数和清单行；结论必须可执行、可复核。

**何时使用**：给定研究目标、约束与科研材料，需要生成、比较、优化或解释实验方案，并明确假设、变量、对照、步骤、参数、资源、风险与异常处理时。**不适用**：实验执行、数据分析、文献综述、论文写作；也不用于无任何真实材料时的"编造方案"。

## 2. 输入契约

运行时输入目录为 `/input`，至少包含：

- `/input/case.json`：案例主输入。可能含 `case_id`/`id`/`challenge_id`、研究目标、`plans`（候选方案，组织方口径为 **3 个带版本号的方案**）、资源/价格/预约合同、有限参数域、样本与伦理信息、以及可选 `output_contract`。
- `/input/task.md`：题目说明与评分口径。
- 其余材料按实际提供：`materials/`、`sources/`、`notes.md`、PDF 等。**读实际文件，不要凭文件名/领域标签假设内容**；PDF/图片用平台解析/OCR 工具取文本，脚本不臆测其内容。

工作目录 `/app/work`，最终产物写入 `/app/output/`（含 `result.json`）。**发现而非假设**：用 `scripts/main.py` 扫描输入，容错 BOM、坏编码、坏 JSON、超 2MB 文件；不假设固定键名。若 `case.json` 含 `output_contract`，其全部字段名必须**原样回填**进结果（缺值给 `null`/`[]`，不更名、不删键）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少含：

- 题目 `output_contract` 全部字段（原样回填）+ `case_id` + `status`（`ok | partial | insufficient_evidence | input_error`）。
- `review`：七类约束 `assumptions_variables`、`controls`、`equipment_materials`、`time_cost`、`parameter_ranges`、`risks_exceptions`、`ethics_safety`，每类为审查项数组。
- `arithmetic_ledger`：每项含 `expr`+`terms`+`result`+`recompute_ok`，可被 `safe_eval` 复算。
- `plan_comparison`：多版候选方案加权排序、`recommended`、权重敏感性（有则填）。
- `evidence_index`：去重后的全部证据片段；`sources`、`uncertainty`、`pending_human`、`confirmation`、`errors`、`degradations`、`limitations`、`change_log`、`runtime`、`artifacts`。

审查项结构、状态枚举、台账 schema 与自检口径见 `references/output_schema.md`；七类判定标准与算术规则见 `references/review_rubric.md`。

- `verdict`：`ok | conditional | blocked | unconfirmed | missing | conflict`。
- `resource_state`（仅资源维度）：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。
- `conditional` 必须写清 `conditions`；`ok/conditional/blocked/conflict` 必须带 `evidence`。

另写 `/app/output/review_report.md`（与 result.json 同源、逐条列证据定位）及 `arithmetic_ledger.csv`、`plan_score_matrix.csv` 供人工复核。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进。** 每个阶段结束都重写 `result.json`（写盘失败则降级为最小合法产物，进程不得崩溃）。

| 步 | 时间窗 | 请求 | 输入 → 输出 |
|---|---|---|---|
| S0 启动骨架 | 0–1 分钟 | ≤3 | `/input` → `scripts/main.py --stage skeleton`，产出合法 partial `result.json` + `input_inventory.json` |
| S1 发现与阅读 | 1–3 分钟 | ≤12 | 读 `case.json`/`task.md`/材料 → 提取研究目标、3 版方案、资源/价格/预约合同、参数域、样本与伦理信息；PDF 用平台工具取文本后补证据 |
| S2 七类审查 | 3–8 分钟 | ≤24 | `scripts/main.py --stage analyze` 生成七类审查项 + 交叉核验 + 版本差异；需要内容判断时写 `/app/work/agent_notes.json` 再 `--notes` 合并 |
| S3 算术与比较 | 8–12 分钟 | ≤12 | 成本/时长/样本量/参数域校验/货期/预算全部走 `review_core` 台账并复算；生成版本排序与敏感性 |
| S4 结论与确认 | 12–14 分钟 | ≤8 | `overall_conclusion`、`confirmation.*`、`uncertainty`、`pending_human`、`limitations` |
| S5 自检收尾 | 14–15 分钟 | ≤4 | `scripts/main.py --stage finalize` → `--stage verify` → 修正 FAIL → 补 `change_log`、写报告/CSV、终版落盘 |

S0 后立即判定材料是否足够：无任何可读输入 → `insufficient_evidence` 并列出缺口；`case.json` 无法解析 → `input_error`；否则继续审查，状态保持 `partial` 直到 S5 收口为 `ok` 或保留 `partial`。

`agent_notes.json`（可选）格式：`{"review": {"<维度>": [<审查项>]}, "pending_human": [...], "uncertainty": [...], "sources": [...]}`。合并时按 `id` 替换、`quote_verified=false` 的条目自动降级为 `unconfirmed`，保证 LLM 补充也过证据校验。

## 5. 证据溯源（硬要求）

每条关键结论、数值、图元都要指回**逐字片段**：

- 定位格式：`case.json#plans[0].steps[2]`、`case.json#contracts.equipment[0]`、`materials/<name>.md#<标题>`、`sources/<name>.pdf#p<页>`、`task.md#<行>`。
- `evidence.quote` 是原文里"键: 值"级逐字片段（脚本按去空白包含关系校验，`quote_verified=true` 才算可复核）。
- 冲突不隐藏：同一指标两个来源不一致时两条都记录并标 `verdict="conflict"` 或 notes 注明 `source_conflict`。
- 不虚构引用、DOI、数值、价格、审批状态；缺失写 `pending_human`/`missing`/`unconfirmed`。

## 6. 审查口径要点

核心三条（详见 `references/review_rubric.md`）：

1. **"方案没写" ≠ "方案满足"**：未写即 `missing`/`unconfirmed`，不默认满足。
2. **时间/成本/样本量/参数域必须算出来**：用 `review_core` 的台账做真实算术并写 `expr`，不用"感觉一个数"；不可算就 `not_computable` 并列出缺什么。
3. **结论可执行**：`conditional` 写清条件；`blocked` 写清缺什么；`unconfirmed` 明确"需谁、确认什么、约多久"。

## 7. 自检清单（交付前逐条勾选）

对照评分维度，用 `python3 scripts/main.py --stage verify` 与 `python3 scripts/selftest.py` 自动校验；逐条确认：

**实际效果**

- [ ] 七类约束各至少一条证据化判断，且无"未写当满足"
- [ ] 每条 `ok/conditional/blocked/conflict` 都有 locator + quote（或 ledger 引用）
- [ ] 数值结论有算式与依据，`recompute_ok=true`
- [ ] 指出方案缺失信息与自相矛盾处（`missing/conflict` + `pending_human`）

**使用增益 / 创新增益**

- [ ] 相同方案相同口径可复跑：判断依赖材料原文而非记忆；台账可复算
- [ ] 方案×合同交叉核验、有限参数域校验、版本排序由确定性脚本完成，非口头估计

**可靠体验**

- [ ] `result.json` 合法且含 `case_id`、`status`、契约字段、`sources`、`uncertainty`、`change_log`
- [ ] 空输入/坏 JSON/缺页/超长文档/伦理未定/方案矛盾/二进制材料均有明确处置
- [ ] 重复运行结构一致（脚本确定性）

**生态贡献**

- [ ] `SKILL.md` 写清用途、输入输出、失败处理、阶段预算；脚本与参考文件真实存在、相对路径可执行
- [ ] `review_report.md`/CSV 可编辑，检查项库与规则可复用

## 8. 降级策略（缺数据/缺软件时如实标注）

- 材料不足 → `status="insufficient_evidence"`，在 `uncertainty`/`pending_human` 列缺口，**不编造**方案、数值、结论。
- 坏 JSON/坏编码/超限文件 → 记 `errors`/`degradations`，继续处理其余材料。
- 缺软件（如 PDF 解析不可用）→ 记 `degradations`，标注"该证据未能读取"，不得假装已读。
- 伦理/安全/设备可用性未经审批或无法确认 → `unconfirmed + needs_expert=true`，绝不写成可执行事实。
- 写盘失败 → 降级到最小合法 JSON，退出码 0。

## 9. 脚本与调用

在 Skill 根目录（含本文件与 `scripts/`）下用相对路径调用；仅依赖 Python 标准库（≥3.8）：

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work --stage skeleton   # S0
python3 scripts/main.py --input /input --output /app/output --work /app/work --stage analyze    # S2
python3 scripts/main.py --input /input --output /app/output --work /app/work --stage finalize   # S5
python3 scripts/main.py --stage verify --output /app/output                                     # 自检
python3 scripts/selftest.py                                                                      # 合成小案例自检（非正式答案）
```

若相对路径不可解析，回退到绝对路径 `/skills/weekly-skill/scripts/<name>.py`；不影响结果字段。

## 10. 边界

一个 Skill 只解决"版本化方案可行性审查"一类任务；无真实材料时输出"材料不足"而非示例填充；不预写正式答案；不冒充评测分数或伪造运行结果。版本排序是审查辅助口径，不是测量真值。
