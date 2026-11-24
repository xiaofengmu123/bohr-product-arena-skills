---
name: feasibility-gate-review
description: 科研实验方案可行性“门控审查”Skill：把研究目标转成可检验问题，对假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常、伦理与安全七类约束逐条审查，用确定性脚本做时间/预算/参数域/样本量/设备机时算术与门控，逐字核验每条证据的 locator+quote，产出可复核的 result.json 与可编辑 review_report.md。当需要对给定材料中的候选实验方案做可行性评估、比较与优化时调用。
version: 1.1.0
---

# 实验方案可行性门控审查（Feasibility Gate Review）

## 1. 角色与何时使用

本 Skill 让 Agent 对 1–3 份**候选实验方案**做“能不能开工”的门控审查：不只评技术合理性，还核对设备/材料是否到位、时间与样本是否匹配、参数域是否有据、风险与异常如何处置、伦理与安全是否满足审批路径。每条判定必须落到方案的具体章节、参数与清单行；结论必须可执行、可复核。

**何时使用**：给定研究目标、约束与真实材料，需要生成、比较、优化或解释实验方案，并明确假设、变量、对照、步骤、参数、资源、风险与异常处理时。**不适用**：实验执行、数据分析、文献综述、论文写作；无任何真实材料时只输出“材料不足”，不编造方案。

## 2. 输入契约

运行时输入目录为 `/input`，至少含 `case.json` 与 `task.md`；其余材料按实际提供（`materials/`、`sources/`、`notes.md`、PDF 等）。**读实际文件，不按文件名/领域标签假设内容**；PDF 用平台解析工具取文本，不假设已有答案层。工作目录 `/app/work`，最终产物写 `/app/output/`（含 `result.json`）。

`case.json` 可能含 `output_contract`；其全部字段名必须**原样回填**进 result（缺值给 `null`/`[]`/`""`，不改名、不删键）。案例常见形态为 versioned_feasibility_review：真实科学文献、前瞻性资源/价格/预约合同、每案例 3 版候选方案、有限参数域、需真实算术核验。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少含：

- 题目 `output_contract` 的全部字段（原样）+ `case_id`（缺省 `derived:*` 或 `unknown`）。
- `status`：`ok | partial | insufficient_evidence | input_error`。
- `research_question`、`plan_set`、`review`（七类，见 §6）、`arithmetic`（算式+结果）、`plan_comparison`、`recommendation`、`confirmation`、`evidence_index`、`sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`、`rubric_coverage`、`baseline_comparison`、`change_log`、`runtime`、`replay_digest`、`artifacts`。

审查项（`review.*` 元素）结构：

```json
{"id":"EQ-01","dimension":"equipment_materials","claim":"……","verdict":"conditional",
 "severity":"major","evidence_grade":"E1","plan_refs":["P1"],
 "evidence":[{"locator":"case.json#contracts.equipment[0]","quote":"逐字原文","kind":"contract"}],
 "arithmetic":null,"conditions":["……"],"action":{"owner":"…","by":"开工前","artifact":"…"},
 "confidence":"medium","needs_expert":false,"notes":[]}
```

- `verdict` 枚举：`ok|conditional|blocked|missing|unconfirmed|conflict`（定义见 `references/rubric.md`）。
- `severity`：`blocker|major|minor|info`；`evidence_grade`：`E1` 逐字原文、`E2` 派生算式、`E3` 推断判断。
- `ok/conditional/blocked/conflict` 必须带 `evidence`；`E2` 必须带 `arithmetic.expr+result`。

另写可编辑的 `/app/output/review_report.md`，与 result 同内容、逐条列证据定位，供人工复核。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进。** 每步结束都重写 `result.json`；写盘失败降级为最小合法 JSON，进程退出码 0。

| 步 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| S0 骨架 | 0–1 min | ≤4 | `/input` → 运行 `scripts/main.py`，产出合法 partial `result.json` + `case_ledger.json` + `input_inventory.json` |
| S1 发现阅读 | 1–3 min | ≤12 | 读 case.json/task.md/材料 → 提取目标、3 版方案、合同、参数域、样本与伦理信息 |
| S2 七类审查 | 3–9 min | ≤24 | 材料原文 → 每类 2–4 条 evidence 判断，用 `main.py merge` 增量回填 `review.*` |
| S3 算术门控 | 9–12 min | ≤12 | `scripts/checks.py` 确定性算时间/预算/参数域/样本量/机时 → 合并 `arithmetic` + `plan_comparison` |
| S4 结论确认 | 12–14 min | ≤8 | 门控 → `recommendation`、`confirmation.*`、`uncertainty`、`pending_human` |
| S5 自检收尾 | 14–15 min | ≤4 | `scripts/selftest.py --seal` → 修 FAIL → 补 `change_log`、写 `review_report.md`、终版落盘 |

S0 后立即判材料：无任何可读输入 → `insufficient_evidence`；case.json 无法解析 → `input_error`；否则保持 `partial`，S5 收口为 `ok` 或保留 `partial`（写清缺口）。

## 5. 评分维度对齐（本版核心设计）

官方 5 个评分维度逐条映射到流程步骤与自检项，交付时用 `rubric_coverage` 在 result 中固化为可审计落点；`baseline_comparison.provided_by="platform"`，本 Skill **不生成**对照分数。

| 评分维度 | 分值 | 落点（流程/产物） | 自检项 |
|---|---|---|---|
| 实际效果 | 40 | S2 七类各≥2 条判断；S3 算式；S4 缺口 | C06/C09/C10/C11 |
| 使用增益 | 25 | 同一方案同口径可复跑（确定性脚本+replay_digest） | C06/C13/C16/C19 |
| 创新增益 | 15 | 容量门控算术 + 引文逐字核验（新增能力） | C12/C21 |
| 可靠体验 | 10 | 空输入/坏 JSON/超限/缺页/矛盾均退出码 0 并如实标注 | C01/C02/C15/C18 |
| 生态贡献 | 10 | `references/rubric.md`、`references/checkitems.md` 检查项库 | C17 |

## 6. 七类审查口径（要点）

判定标准、状态枚举、算术规则详见 `references/rubric.md`；可勾选检查项库详见 `references/checkitems.md`。核心三条：

1. **“方案没写” ≠ “方案满足”**：未写即 `missing`，不得默认满足。
2. **时间/预算/参数域/样本量必须算出来**：用 `checks.py` 真实算术并写 `expr`，不凭感觉给数。
3. **结论可执行**：`conditional` 写清条件；`blocked` 写清缺什么；`unconfirmed` 明确“需谁、确认什么、约多久”。

## 7. 证据溯源与引文核验（硬要求）

- 定位格式：`case.json#plans[0].steps[2]`、`case.json#contracts.equipment[0]`、`materials/<name>.md#<行/标题>`、`sources/<name>.pdf#p<页>`、`task.md#L<n>`。
- `evidence.quote` 必须是材料中的逐字原文；过长时截取决定性子句并给定位，不转写、不补全。
- 冲突不隐藏：同一指标两来源不一致时两条都记，`verdict="conflict"` 或 `notes` 标 `source_conflict`。
- **逐字核验**：S5 由 `selftest.py C09` 重新读取 `/input`，按空白归一后做子串匹配，任一 quote 未命中即 FAIL。
- 不虚构引用、DOI、数值、价格、审批状态；缺失写 `pending_human` 或 `missing`。

## 8. 自检清单（交付前逐条勾选，对应评分维度）

- [ ] 七类约束各至少 2 条（partial 至少 1 条）evidence 化判断，无“未写当满足”（C06/C11）
- [ ] `ok/conditional/blocked/conflict` 均有 `locator+quote`，且逐字核验通过（C09）
- [ ] 数值结论有 `expr+result+unit`（时间/预算/参数域/样本量/机时）（C10/C12）
- [ ] 指出方案缺失信息与自相矛盾处，非 ok 状态写清缺口（C02/C15）
- [ ] 相同方案相同口径可复跑：脚本确定性 + `replay_digest`（C19）
- [ ] `result.json` 合法且含 `case_id`、`status`、契约字段（C01/C03/C04）
- [ ] 多方案给出确定性排序（C13）；伦理相关时确认项非空（C14）
- [ ] 空输入/坏 JSON/缺页/超长/伦理未定/方案矛盾均有处置（C01/C02/C15/C18）
- [ ] `change_log` rev 严格递增（C16）；评分维度覆盖表齐备（C17）
- [ ] 无占位符；对照分数不由本 Skill 生成（C18/C21）

## 9. 降级策略（缺数据/缺软件时如实标注，不编造）

- 材料不足 → `insufficient_evidence`，在 `uncertainty`/`pending_human` 列缺口。
- 坏 JSON/坏编码/超限文件 → 记入 `errors`/`degradations`，继续处理其余材料。
- 缺软件（如 PDF 解析不可用）→ 记入 `degradations`，标注“该证据未能读取”。
- 伦理/安全/设备可用性未经审批或无法确认 → `unconfirmed` + `needs_expert=true`，不写成可执行事实。
- 写盘失败 → 降级到最小合法 JSON，进程退出码 0。

## 10. 脚本与调用

在 Skill 根目录（含 `scripts/`）下用相对路径调用；仅依赖 Python 标准库（≥3.8）：

```bash
python3 scripts/main.py                                   # S0 骨架
python3 scripts/main.py merge --patch <patch.json>        # S2–S4 增量合并
python3 scripts/checks.py --work /app/work --print        # S3 算术与门控
python3 scripts/selftest.py --input /input --output /app/output --seal   # S5 自检
```

相对路径不可解析时回退绝对路径 `/skills/weekly-skill/scripts/<name>.py`；不影响结果字段。`checks.py` 与 `selftest.py` 均支持 `--help`。

## 11. 边界

一个 Skill 只解决“方案可行性门控审查”一类任务；无真实材料时输出“材料不足”而非示例填充；不预写正式答案；不冒充评测分数或伪造运行结果；规划场景中的价格/预约/审批状态是前瞻性合同，不声称是已发生的真实采购或获批。
