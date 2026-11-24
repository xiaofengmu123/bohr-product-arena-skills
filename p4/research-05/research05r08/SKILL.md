---
name: research-selection-assessment
description: 科研选题与创新性评估 Skill。把固定研究问题拆成可检验表述，对已有工作建立可比较证据矩阵，逐条判定创新点覆盖与可行性前提，所有结论回链到材料原文位置（locator+quote），并区分材料事实、合理推断与无法确定项，输出可复核的 result.json 与 report.md。适用于立项/投稿/申报前的选题评估、文献证据综述与边界说明。
version: 2.0.0
---

# 科研选题与创新性评估 Skill

## 1. 角色与适用场景

**角色**：立项/投稿/申报前的选题评估执行者。只依据 `/input` 中的材料做判断，把每条结论落到可回查的证据位置，不替作者补造材料、不把推断写成事实。

**何时使用**：给定一个研究问题与一组文献/材料，需要回答四件相连的事——问题能否拆成可检验表述、已有工作做到哪一步、增量在哪里、现有条件能否做成。输出问题定义、已有工作比较、创新点识别、可行性判断四份相互一致的交付，并附证据回链、冲突证据与边界说明。

**不适用**：一次性脚本；纯聊天问答；无任何可读研究材料却要求给结论（此时返回 `insufficient_evidence`）。

## 2. 输入契约

运行时固定输入位于 `/input`：

- `/input/case.json`：任务元信息、材料清单、`output_contract`（若有，其全部字段必须原样回填并提升到顶层）。
- `/input/task.md`：任务说明与评分要点。
- 材料文件：PDF（用 `pdftotext` 抽文字）、`.md/.txt/.json/.csv/.xml/.html` 等文本，及办公二进制，按扩展名识别，不假设固定文件名。

硬规则：

- 文本按 `utf-8` 读取，失败降级为替换符并记录；坏 JSON/坏编码记入 `errors`/`degradations`，不崩溃。
- 单文件 > 2MB 跳过并记录；不因文件名/标签猜内容，实际读文件。
- `/input` 缺失或 `case.json` 缺失 → `input_error`；有输入但无可读研究材料 → `insufficient_evidence`。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`。`result.json` 顶层字段：

| 字段 | 要求 |
|---|---|
| `case_id` | 保留输入标识；缺省时按输入目录哈希回退并标 `unresolved-` |
| `status` | `ok` / `partial` / `insufficient_evidence` / `input_error`，诚实取值 |
| `output_contract` | 原样回显 case.json 的 `output_contract`；其每个键同时提升到顶层，缺值给 `null`/`[]`，不改名不删未知键 |
| `research_question` / `search_protocol` / `prior_work` / `novelty` / `feasibility` | 四项交付与检索协议（见第 4 节） |
| `evidence_matrix` | 每条判断的证据：`source_id`、`locator`、`quote`（逐字）、`field`、`claim`、`claim_type`、`status` |
| `conflicts` | 相反证据登记：`topic`、`side_a`、`side_b`、`resolution` |
| `synthesis` | `conclusions`、`research_gaps`、`boundary_statement`（只到证据范围为止） |
| `sources` / `uncertainty` / `pending_human` / `change_log` | 来源清单；偏倚/空白/无法确定项；待人工确认；按时间顺序的改动记录 |
| `errors` / `degradations` / `runtime` | 错误、降级、运行统计 |

每条 `evidence_matrix` 记录必须含 `locator`（指向 `/input` 内相对路径与行号，如 `materials/paper.md#L12`；PDF 行号以 pdftotext 抽取文本为准，抽取结果存 `/app/work/text/`）与逐字 `quote`。`claim_type` 取 `fact|inference|unknown`，用于区分材料事实、合理推断与无法确定项。无引用信息写 `not_reported`，不造 DOI/数值。

## 4. 执行流程（总预算 900 s / 64 次模型请求 / 单响应 ≤16384 tokens）

请求纪律：一次读一段，不把整本 PDF 灌进一个响应；写完一项立即落盘，不反复重读同一文件。所有命令在 Skill 根目录（`/skills/weekly-skill`）下用相对路径执行。

**Phase 0（0–3 min，必须最先执行）**：先落盘骨架，再做任何分析。

```bash
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
```

写入 `status=partial` 的合法 `result.json`（契约字段齐全、内容 `null/[]`）与 `/app/work/input_manifest.json`。此后任何失败都至少有合法部分结果。

**Phase 1（3–6 min）**：发现材料、抽 PDF 文字。

```bash
python3 -B scripts/main.py evidence --input /input --work /app/work --output /app/output
```

生成 `/app/work/material_inventory.json`，刷新 `sources`；无可用研究材料时置 `insufficient_evidence`。随后读 `task.md` + `case.json`，确认四项交付是否全部要求。

**Phase 2（6–11 min）**：四项交付，逐项写 fragment 落盘 `/app/work/fragments/`。每完成一项写一个 JSON 对象（`01_question.json`、`02_prior.json`、`03_novelty.json`、`04_feasibility.json`），键名与 result.json 对应，只放该项内容：

1. 研究问题定义：拆成研究对象、关键变量、比较基线、预期结论、可证伪条件 → 可检验陈述；缺项标 `status=partial/missing` 并列缺口。
2. 已有工作比较：按任务/方法/数据/评价方式/结论建比较矩阵，识别真正相近工作，标出不可比原因；禁止跨文档串值。
3. 创新点识别：区分方法创新/数据创新/场景迁移/结论修正，逐条判定覆盖，`verdict` 取 `novel|covered|partially_covered|unverifiable`，给理由+证据。
4. 可行性判断：核查数据/算力/实验条件/时间窗口/人员能力等前提；每条标 `reported|assumed|missing|conflicting`，输出带条件结论与失败风险。

**Phase 3（11–14 min）**：综合、冲突、不确定性。补 `evidence_matrix`（关键判断回链 locator+quote）、`conflicts`（相反证据不隐藏，写明取舍）、`synthesis`（结论+研究空白+边界）、`uncertainty`（偏倚/空白/无法确定项）。

**Phase 4（14–15 min）**：合并、校验、收尾。

```bash
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```

深合并所有 fragments 到骨架，校验契约字段齐全、无 NaN、证据定位非空，写最终 `result.json` 与 `report.md`。剩余时间只做增量修正（追加/改写 fragment 后重跑 finalize）。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 可检验问题：`research_question.testable_statement` 含对象/变量/基线/可证伪条件（实际效果·任务完成度）。
- [ ] 四项交付齐全且一致：问题/比较/创新/可行性引用同一证据集（实际效果·正确性）。
- [ ] 六类内容覆盖：问题拆解/检索覆盖/证据回链/冲突证据/综合结论/研究空白（实际效果·完整性）。
- [ ] 可核验：每条结论在 `evidence_matrix` 有 locator+逐字 quote；检索式/纳排规则写入 `search_protocol`（实际效果·可核验性）。
- [ ] 增益可复核：`change_log`、`evidence_matrix`、`report.md` 三处一致（使用增益）。
- [ ] 创新边界：每条 novelty 有 `covered_by` 与 `verdict`；场景迁移/结论修正未当方法创新（创新增益）。
- [ ] 可行性前提有出处：每条 feasibility 标 `reported|assumed|missing|conflicting`，assumed 显式标注（可靠体验）。
- [ ] 冲突不隐藏：相反证据进 `conflicts` 并说明取舍；"未报告"≠"不存在"（可靠体验）。
- [ ] 诚实状态：`status` 与 `errors/degradations/pending_human` 一致，无编造（可靠体验）。
- [ ] 骨架先行：Phase 0 已在 3 min 内落盘 result.json（运行稳定性）。
- [ ] 可复跑：同一输入两次运行结论一致（生态贡献）。

评分维度逐条映射见 `references/scoring-map.md`；四项交付判定口径与证据字段见 `references/assessment-rules.md`。提交前运行 `python3 -B scripts/selftest.py`，退出码 0 为通过。

## 6. 降级与诚实性

- 缺数据：对应字段保持 `null/[]` 并写入 `uncertainty.gaps`，status 降级；不编造数据、DOI、分数或运行结果。
- 缺软件（如 `pdftotext` 不可用）：evidence 阶段记入 `degradations`，PDF 标 `text_unavailable`，仍处理其余文本材料。
- 冲突/偏倚/空白：全部登记到 `conflicts` 与 `uncertainty`；不确定结论标 `claim_type=inference` 或 `unknown`，不写成 `fact`。
- 写盘失败：脚本降级写最小合法结果，不崩溃；退出码 0（finalize 校验失败返回 2 示需修正）。

## 7. 脚本与资源

- `scripts/main.py`：`skeleton` / `evidence` / `finalize` 三个子命令（先跑 skeleton）。
- `scripts/selftest.py`：本地自检，覆盖缺输入/坏 JSON/无材料/正常 demo 与契约校验。
- `references/scoring-map.md`：评分维度 → 流程 → 输出字段 → 自检项映射。
- `references/assessment-rules.md`：四项交付判定口径与证据字段规范。

脚本仅依赖 Python 标准库（`pdftotext` 为平台预装工具，缺失时降级）。`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。

**最小调用示例（示意，非正式案例输出）**：

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py evidence --input /input --work /app/work --output /app/output
# Agent 依第 4 节逐项写入 /app/work/fragments/*.json（示意输出须在 report.md 标明为示例）
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py
```
