---
name: experiment-feasibility-review
description: 科研实验方案可行性审查 Skill，把研究目标转成可检验问题，对假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常、伦理与安全七类约束逐条证据化审查并做算术校验，产出可复核可执行的 result.json 与可编辑审查报告，用于对给定材料中的候选实验方案做可行性评估、比较与优化时调用。
version: 1.1.0
---

# 实验方案可行性审查（精简版）

## 1. 用途与边界

本 Skill 让 Agent 对候选实验方案做"可开工性"审查：不只评技术合理性，还要逐条核对设备/材料是否到位、时间/样本是否匹配、参数范围是否有据、风险与异常如何处置、伦理与安全是否满足审批路径，并把结论落到方案的具体章节、参数与清单行。

- **适用**：给定研究目标、约束与真实材料，需要生成/比较/优化/解释实验方案，并明确假设、变量、对照、步骤、参数、资源、风险与异常时。
- **不适用**：实验执行、数据分析、文献综述、论文写作；无任何真实材料时不得编造方案、数值或结论。

## 2. 输入契约

运行时输入目录 `/input`（至少含 `case.json` 与 `task.md`），工作目录 `/app/work`，产物写入 `/app/output/`。其余材料按实际存在读取（`materials/`、`sources/`、PDF 等）；PDF 用平台解析工具取文本，不得假设已有答案层。

**发现而非假设**：用 `scripts/main.py` 扫描输入目录，容错 BOM/坏编码/坏 JSON/超 2MB 文件，不假设固定键名。若 `case.json` 含 `output_contract`，其全部字段名必须原样回填进结果（缺值给 `null`/`[]`，不更名、不删键）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少含以下字段：

| 字段 | 说明 |
|---|---|
| `case_id` 及题目 `output_contract` 全部字段 | 原样回填 |
| `status` | `ok \| partial \| insufficient_evidence \| input_error` |
| `overall_conclusion` | `verdict / summary / conditions / blocks / notes` |
| `experiment_plan` | `research_question / assumptions / variables / controls / steps / parameters / resources / interpretation_criteria` |
| `review` | 七类约束，每类为审查项数组 |
| `plan_comparison` | 多版候选方案比较（有则填） |
| `evidence_and_risks` | `evidence / risks / exception_handling` |
| `confirmation` | `safety / ethics / equipment_availability / expert_confirmation` |
| `sources` / `uncertainty` / `pending_human` / `limitations` | 证据、不确定性、待人工、缺口 |
| `errors` / `degradations` / `change_log` / `runtime` / `artifacts` | 运行状态与诚实标注 |

审查项结构（`review.*` 元素，可加 `computation` 字段承载算式）：

```json
{"id":"eq-1","dimension":"equipment_materials","claim":"…","verdict":"conditional",
 "resource_state":"需采购","conditions":["采购前需确认…"],"computation":"…",
 "evidence":[{"locator":"case.json#plan.0.steps[2]","quote":"逐字原文","kind":"plan"}],
 "confidence":"medium","needs_expert":false,"notes":[]}
```

- `verdict` 枚举：`ok | conditional | blocked | unconfirmed | missing | conflict`（含义见 `references/rubric.md`）。
- `resource_state` 仅资源维度使用：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。
- `conditional` 必须写清逐条可执行 `conditions`；`ok/conditional/blocked/conflict` 必须带 `evidence`。

另写一份可编辑的 `/app/output/review_report.md`，与 result.json 同内容、逐条列出证据定位，供人工复核（结构模板见 `references/report_template.md`）。

## 4. 执行流程（预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进；每步结束都重新保存 result.json，写盘失败则降级到最小合法产物。**

| 步 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| S0 启动骨架 | 0–1 分钟 | ≤4 | 运行 `main.py` → partial `result.json` + `input_inventory.json` |
| S1 发现与阅读 | 1–3 分钟 | ≤12 | 读 `case.json`/`task.md`/材料 → 研究目标、候选方案、资源合同、参数域、样本与伦理 |
| S2 七类审查 | 3–9 分钟 | ≤24 | 材料原文 → 每类 2–4 条证据化判断 |
| S3 算术与比较 | 9–12 分钟 | ≤12 | 用 `review.py` 做时间/参数/样本量/成本校验与方案排序 |
| S4 结论与确认 | 12–14 分钟 | ≤8 | → `overall_conclusion`、`confirmation.*`、`uncertainty`、`pending_human` |
| S5 自检收尾 | 14–15 分钟 | ≤4 | `selfcheck.py` → 修正 FAIL → 补 `change_log`、写 `review_report.md` |

S0 后立即判定：无可读输入 → `insufficient_evidence` 并列出缺口；`case.json` 无法解析 → `input_error`；否则保持 `partial` 直到 S5 收口为 `ok` 或保留 `partial`。

## 5. 七类审查口径（详见 references/rubric.md）

每类至少 2–4 条证据化判断；"完成标准"写进 `overall_conclusion.conditions/blocks`。

1. `assumptions_variables`：假设可检验；自变量/因变量/混杂变量明确、测量方式可复核。**完成标准**：实验人员能据此界定变量，或明确知道缺哪个定义。
2. `controls`：空白/阳性/阴性对照、随机化与盲法是否完整。**完成标准**：对照能排除关键混杂，缺对照即 `conditional`。
3. `equipment_materials`：型号、精度、附件、耗材规格 vs 现有条件清单，判 `resource_state`。**完成标准**：区分已具备/可外协/需采购/不具备。
4. `time_cost`：设备占用 + 人工操作 + 等待周期 + 检测周转，必须给出算式。**完成标准**：给出总时长与逐项依据，而非一个数。
5. `parameter_ranges`：每个参数域有依据与容差，并给出越界判定。**完成标准**：参数与容差可复核，越界有处置。
6. `risks_exceptions`：设备故障、批次差异、样本量不足、参数越界的降级路径与转人工节点。**完成标准**：出问题时有明确降级/替代方案。
7. `ethics_safety`：动物/人体样本/危险试剂/个人数据 → 审批级别、材料、大致时长。**完成标准**：明确审批路径与材料清单。

核心三条：**"方案没写" ≠ "方案满足"**（未写即 `missing`）；**数值必须算出来**（用 `review.py` 的 `sum_time`/`check_range`，不用"感觉一个数"）；**结论可执行**（`conditional` 写清条件、`blocked` 写清缺什么、`unconfirmed` 明确"需谁、确认什么、约多久"）。

## 6. 证据溯源与诚实标注

- 每条关键结论/数值给 `locator + 逐字 quote`：`case.json#plan.<i>.steps[<j>]`、`case.json#contracts.<key>`、`materials/<name>.md#<标题>`、`sources/<name>.pdf#p<页>`、`task.md#<行>`。
- `quote` 必须是材料逐字原文，过长时截取决定性子句并给定位；不转写、不补全、不虚构 DOI/价格/审批状态。
- 冲突不隐藏：同一指标两源不一致时两条都记录，标 `conflict` 或 `notes=["source_conflict"]`。
- 缺软件（如 PDF 无法解析）→ 记入 `degradations` 与 `limitations`，标注"该证据未能读取"。

## 7. 降级策略（缺数据/缺软件时如实标注，不编造）

| 情形 | 处置 |
|---|---|
| 输入目录为空/无可读文件 | `status="insufficient_evidence"`，在 `limitations` 与 `pending_human` 列缺口 |
| `case.json` 无法解析 | `status="input_error"`，记 `errors`，仍保留契约骨架 |
| 坏编码/超 2MB/缺页 | 记 `degradations`，继续处理其余材料 |
| PDF 解析工具不可用 | 记 `degradations`，`limitations` 注明"该证据未能读取"，不假装已读 |
| 伦理/安全/设备可用性未确认 | `verdict="unconfirmed"` + `needs_expert=true`，不写成可执行事实 |
| 写盘失败 | 降级到最小合法 JSON，进程退出码 0 |

## 8. 自检与交付（交付前逐条勾选）

运行 `python3 scripts/selfcheck.py --input /input --output /app/output`，并逐条确认：

- [ ] `result.json` 合法且含 `case_id`、`status`、契约字段
- [ ] 七类约束各至少一条证据化判断，且无"未写当满足"
- [ ] 每条 `ok/conditional/blocked/conflict` 都有 `locator + quote`
- [ ] 数值结论有算式与依据（时间累加、参数范围、样本量）
- [ ] 指出方案自身缺失的信息与自相矛盾处
- [ ] 空输入/坏 JSON/缺页/超长文档/伦理未定/方案矛盾均有明确处置
- [ ] 重复运行结构一致（脚本确定性）

## 9. 脚本调用（Skill 根目录下，仅 Python 标准库 ≥3.8）

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work
python3 scripts/review.py --input /input --output /app/output   # 打印 schema/枚举与算术校验示例
python3 scripts/selfcheck.py --input /input --output /app/output
```

若相对路径不可解析，回退到绝对路径 `/skills/weekly-skill/scripts/<name>.py`；不影响结果字段。

## 10. 最小调用示例（示意，非正式案例结果）

输入：`/input/case.json` 含 `title`、`goal`、`plans`、`resources` 与 `output_contract`；`/input/task.md` 为题面。

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work
# → 打印 status/case_id/output_contract_keys；落盘 result.json + input_inventory.json
python3 scripts/review.py --demo        # 打印七类维度、verdict/resource_state 枚举、算术示例
python3 scripts/selfcheck.py --input /input --output /app/output
```

示意输出片段（**示例，非真实运行结果**）：

```json
{"status":"partial","case_id":"demo:case","output_contract_keys":["feasibility_summary","top_risk"]}
```

## 11. 边界

一个 Skill 只解决"方案可行性审查"一类任务；无真实材料时输出"材料不足"而非示例填充；不预写正式答案；不冒充评测分数或伪造运行结果。
