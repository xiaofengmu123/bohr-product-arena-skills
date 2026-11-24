---
name: experiment-feasibility-audit
description: >-
  实验方案可开工性审计：把研究目标转成可检验问题与变量，对假设与变量、对照组、设备材料、
  时间成本、参数范围、风险异常、伦理安全七类约束逐条比对原始材料与资源/价格/预约合同，
  用确定性脚本核算工期、费用、参数越界与样本缺口，产出带原文定位（locator + 逐字引文 +
  内容指纹）的 result.json 与可编辑审查报告。适用于给定 /input/case.json、task.md 与
  随附材料时的方案可行性评估、多版本比较与优化；不适用于实验执行、数据分析、文献综述、
  论文写作，也不用于无真实材料时编造方案。
version: 2.1.0
---

# 实验方案可开工性审计（Experiment Feasibility Audit）

## 1. 角色与何时使用

本 Skill 让 Agent 对一份或多份候选实验方案做"能不能开工"的审查：不只评技术合理性，
还把设备是否到位、材料货期、时间与样本是否匹配、参数范围是否有据、风险与异常如何处置、
伦理与安全能否过审批，逐条落到方案的具体章节、参数和清单行。判定必须可执行、可复核。

**触发**：给定研究目标、约束与科研材料，需要生成、比较、优化或解释实验方案，并明确假设、
变量、对照、步骤、参数、资源、风险与异常处理时。**不适用**：实验执行、数据分析、文献综述、
论文写作；无真实材料时只输出"材料不足"，不做示例填充。

## 2. 本版设计取舍（鲁棒性优先）

1. **先落盘骨架**：启动即写合法 partial `result.json`，中途异常不留空产物。
2. **确定性内核**：工期/费用/参数/样本全部走脚本函数并输出算式，同输入同 `run_id`。
3. **证据三元组**：每条关键结论给 `locator + 逐字 quote + 内容指纹`，冲突信息不隐藏。
4. **优雅降级**：空输入、坏 JSON、缺参数、超大文件、不可写输出都退出码 0 并如实记录。
5. **报告同源**：`review_report.md` 由 result.json 渲染，杜绝结构化结果与报告漂移。

## 3. 输入契约

- `/input/case.json`：案例主输入，可能含 `case_id`/`id`/`challenge_id`、研究目标、
  `plans`（1–3 版候选方案）、资源/价格/预约合同、参数域、样本与伦理信息、
  可选 `output_contract`。
- `/input/task.md`：题目说明与评分口径。
- 其余材料按实际提供：`materials/`、`sources/`、`notes.md`、PDF 等。**读实际文件**，
  不凭文件名/领域标签假设内容；PDF 用 pdftotext 取文本，不假设已有答案层。

工作目录 `/app/work`，最终产物写 `/app/output/`。**发现而非假设**：用
`scripts/ingest.py` 扫描输入，容错 BOM、坏编码、坏 JSON、超 2MB 文件；不假设固定键名。

## 4. 输出契约

必须产出 `/app/output/result.json`，顶层含：

- `case_id`、`status`（`ok | partial | insufficient_evidence | input_error`）、
  `research_question`、`run_id`、`case_fingerprint`、`budget`。
- 题目 `output_contract` 的全部字段**原样回填**到顶层（缺值 `null`/`[]`/`{}`/`""`，
  不改名、不删键）；回填记录在 `output_contract`。
- `experiment_plan`：假设、变量、对照、步骤、参数、资源、判读标准。
- `review`：七类约束 `assumptions_variables`、`controls`、`equipment_materials`、
  `time_cost`、`parameter_ranges`、`risks_exceptions`、`ethics_safety`，每类为审查项数组。
- `plan_comparison`：多版候选方案比较（有则填）。
- `evidence_and_risks`、`confirmation`、`overall_conclusion`。
- `sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`、
  `change_log`、`runtime`、`artifacts`。

审查项结构、verdict 状态机与算术规则见 `references/review_rubric.md`；降级矩阵见
`references/robustness_matrix.md`。另写 `/app/output/review_report.md`（由脚本渲染，
与 result.json 同内容、逐条列证据定位，供人工复核）。

## 5. 证据溯源（硬要求）

- 定位格式：`case.json#plans.<i>.steps[<j>]`、`case.json#contracts.<key>`、
  `materials/<name>.md#<标题或行>`、`sources/<name>.pdf#p<页>`、`task.md#<行>`。
- `evidence.quote` 必须是材料逐字原文；过长截取决定性子句并给定位，不转写、不补全。
- 冲突不隐藏：同指标两来源不一致，两条都记录并标 `verdict="conflict"` 或
  `notes="source_conflict"`。
- 不虚构引用、DOI、数值、价格、审批状态；缺失写 `pending_human` 或 `missing`。

## 6. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进。** 每步结束都
`--merge` 写回 result.json（写盘失败降级到最小合法产物，进程不崩溃）。

| 步 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| S0 启动骨架 | 0–1 min | ≤4 | `/input` → 运行 `ingest.py`，产出 partial `result.json` + `input_inventory.json` + `brief.json` |
| S1 发现与阅读 | 1–4 min | ≤14 | 读 `brief.json` 的 plans/contracts/domains/ethics → 提取目标、方案、合同、参数域、样本与伦理 |
| S2 七类审查 | 4–10 min | ≤26 | 材料原文 → 每类 2–4 条证据化判断，逐段 `--merge` 回填 `review.*` |
| S3 算术与比较 | 10–12 min | ≤10 | 工期/费用/参数/样本 → `audit.py --calc` 批量运算，回填 `arithmetic` 与 `plan_comparison` |
| S4 结论与确认 | 12–14 min | ≤6 | 各维判断 → `overall_conclusion`、`confirmation.*`、`uncertainty`、`pending_human` |
| S5 自检收尾 | 14–15 min | ≤4 | `selftest.py` 自检 → 修 FAIL → `--render-report` 出报告 → 终版落盘 |

S0 后立即判定材料是否足够：无任何可读输入 → `insufficient_evidence` 并列缺口；
`case.json` 无法解析 → `input_error`；否则保持 `partial`，直到 S5 自检通过才可改 `ok`。

## 7. 命令（在 Skill 根目录用相对路径调用）

```bash
python3 scripts/ingest.py --input /input --output /app/output --work /app/work
python3 scripts/audit.py --schema
python3 scripts/audit.py --calc /app/work/ops.json --out /app/work/calc.json
python3 scripts/audit.py --merge /app/work/patch.json --into /app/output/result.json --note "S2 设备"
python3 scripts/audit.py --render-report /app/output/result.json -o /app/output/review_report.md
python3 scripts/selftest.py --input /input --output /app/output --work /app/work
python3 scripts/selftest.py --faults
```

仅依赖 Python 标准库（≥3.8）。相对路径不可解析时回退
`/skills/weekly-skill/scripts/<name>.py`，不影响结果字段。

## 8. 自检清单（交付前逐条勾选）

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
- [ ] 空输入/坏 JSON/缺参数/超大文件/伦理未定/方案矛盾均有明确处置
- [ ] 重复运行结构一致（脚本确定性）

**生态贡献**

- [ ] `SKILL.md` 写清用途、输入输出、失败处理；脚本与参考文件真实存在、相对路径可执行
- [ ] `review_report.md` 可编辑、模板与检查项可复用

## 9. 降级策略（缺数据/缺软件时如实标注，不编造）

- 材料不足 → `status="insufficient_evidence"`，在 `uncertainty`/`pending_human` 列缺口。
- 坏 JSON/坏编码/超限文件 → 记 `errors`/`degradations`，继续处理其余材料。
- 缺软件（如 pdftotext 不可用）→ 记 `degradations` 标"该证据未能读取"，不得假装已读。
- 伦理/安全/设备可用性未经审批或无法确认 → `unconfirmed + needs_expert=true`。
- 写盘失败 → 降级到最小合法 JSON 走 stdout，退出码 0。
- 所有降级与假设统一写入 `limitations`，与结论同层、可被复核。

## 10. 边界

一个 Skill 只解决"方案可行性审计"一类任务；无真实材料时输出"材料不足"而非示例填充；
不预写正式答案；不冒充评测分数或伪造运行结果。
