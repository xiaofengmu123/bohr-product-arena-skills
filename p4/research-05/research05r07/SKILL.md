---
name: sci-research-selection-review
description: 科研选题与创新性评估 Skill。把固定研究问题拆成可检验表述，对已有工作建立可比较证据矩阵，逐条判定创新点覆盖与可行性前提，所有结论回到材料原文位置（locator+quote），先落盘 partial 结果骨架再做增量改进，输出可复核的 result.json 与 report.md。当需要对研究选题做立项前评估、文献证据综述与边界说明时使用。
version: 1.1.0
---

# 科研选题与创新性评估 Skill

## 1. 角色与适用场景

**角色**：立项/投稿前选题评估的执行者。只依据 `/input` 中实际读取的材料做判断，把结论落到可回查的证据位置，不替作者补造材料。

**何时使用**：给定研究问题 + 一组文献/材料，需回答四件相连的事——问题是否可检验、已有工作做到哪一步、增量在哪里、现有条件能否做成。输出四份相互一致的交付：问题定义、已有工作比较、创新点识别、可行性判断，并附证据回链与冲突证据。

**不适用**：一次性脚本；纯聊天问答；材料为零但仍要求结论的场景（返回 `insufficient_evidence`）。本版侧重**鲁棒性**：空输入、坏 JSON、缺参数、超大文件、坏编码均优雅降级，不崩溃、不编造。

## 2. 输入契约

运行时固定输入位于 `/input`：

- `/input/case.json`：任务元信息、材料清单；可能含 `output_contract`（若有，其全部字段必须原样回填）。
- `/input/task.md`：任务说明与评分要点。
- 材料文件：PDF（用 `pdftotext` 抽文字）、`.md/.txt/.json/.csv/.xml/.html` 等文本、`.docx`；按扩展名识别，不假设固定文件名。

硬规则：

- 文本一律容错读取（`utf-8-sig`→`utf-8`→`latin-1` 兜底），坏 JSON/坏编码记录到 `errors`/`degradations`，不崩溃。
- 文本文件超 2MB 跳过并记录；PDF 超 100MB 跳过；抽取文本超 5MB 截断并记录。
- 不因标签猜内容，**实际读文件**。
- `case.json` 缺失或输入目录缺失 → `input_error`；有输入但无可读材料 → `insufficient_evidence`。

## 3. 输出契约

必须产出 `/app/output/result.json`，并同时产出 `/app/output/report.md`。顶层字段：

| 字段 | 要求 |
|---|---|
| `case_id` | 保留输入标识；缺省时按输入目录哈希回退并标 `unresolved-` |
| `status` | `ok` / `partial` / `insufficient_evidence` / `input_error`，诚实取值 |
| `output_contract` | 原样回显 case.json 的 output_contract；其每个键同时回填到顶层，缺值给 `null`/`[]`，不改名不删未知键 |
| `research_question` / `search_protocol` / `prior_work` / `novelty` / `feasibility` | 四项交付 + 检索协议（见第 4 节） |
| `evidence_matrix` | 每条判断的证据：`source_id`、`locator`、`quote`（逐字）、字段、状态 |
| `conflicts` | 相反证据登记：`topic`、两方记录、取舍理由 |
| `synthesis` | `conclusions`、`research_gaps`、`boundary_statement`（只到证据范围为止） |
| `sources` / `uncertainty` / `change_log` | 来源清单；偏倚/空白/无法确定项；按时间顺序的改动记录 |
| `pending_human` / `errors` / `degradations` / `limitations` / `runtime` | 待人工确认、错误、降级、局限、运行统计 |

证据记录必须含 `locator`（如 `materials/paper.txt` 行号或 `task.md#L3`）与逐字 `quote`；无引用信息就写 `pending_human` 或 `not_reported`，不造 DOI/数值。

## 4. 执行流程（总预算 900 s / 64 次模型请求 / 单响应 ≤16384 tokens）

**请求纪律**：一次读一段，不把整本 PDF 灌进一个响应；写完一项立即落盘，不反复重读同一文件。

### Phase 0（0–3 分钟，必须最先执行）：先落盘骨架

```bash
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
```

写 `/app/output/result.json`（`status=partial`，全部契约字段就位、内容为 `null/[]`）与 `/app/work/input_manifest.json`。此后任何失败都至少有合法部分结果。**前 3 分钟完成本步是硬要求**，之后只做增量改进。

### Phase 1（3–6 分钟）：发现材料、抽 PDF 文字

```bash
python3 -B scripts/main.py evidence --input /input --work /app/work --output /app/output
```

生成 `/app/work/material_inventory.json`，刷新 `sources`；无可用材料时把 status 置为 `insufficient_evidence`。随后读 `task.md` + `case.json`，确定四项交付是否全部要求。

### Phase 2（6–11 分钟）：四项交付（每项写成 fragment 落盘）

按顺序完成，每完成一项就写一个 JSON 对象到 `/app/work/fragments/`（如 `01_question.json`、`02_prior.json`、`03_novelty.json`、`04_feasibility.json`），键名与 result.json 一致，只放该项内容：

1. **研究问题定义**：拆成 研究对象、关键变量、比较基线、预期结论、可证伪条件 → 写出可检验陈述；材料没给齐就标 `status=partial/missing` 并列出缺口。
2. **已有工作比较**：按 任务/方法/数据/评价方式/结论 建比较矩阵，识别真正相近的工作，标出不可直接比较的原因；禁止跨文档串值。
3. **创新点识别**：区分 方法创新/数据创新/场景迁移/结论修正，逐条判定是否被已有工作覆盖，`verdict` 取 `novel|covered|partially_covered|unverifiable`，给理由+证据。
4. **可行性判断**：核查 数据、算力、实验条件、时间窗口、人员能力 等前提；每条前提标 `reported|assumed|missing|conflicting`，输出带条件结论与失败风险。

### Phase 3（11–14 分钟）：综合、冲突、不确定性

补 `evidence_matrix`（关键判断回链到 locator+quote）、`conflicts`（相反证据不隐藏，写明取舍）、`synthesis`（结论+研究空白+边界）、`uncertainty`（偏倚/空白/无法确定）。

### Phase 4（14–15 分钟）：合并、校验、收尾

```bash
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```

深合并所有 fragments 到骨架，回查 output_contract 字段齐全、无 NaN、证据定位非空，写最终 `result.json` 与 `report.md`。剩余时间只做增量修正（追加 fragment 后重跑 finalize）。

## 5. 自检清单（对应评分维度）

- [ ] **可检验问题**（任务完成度）：`research_question.testable_statement` 含对象/变量/基线/可证伪条件。
- [ ] **四项交付齐全且一致**（正确性）：问题、比较、创新、可行性相互引用同一证据集。
- [ ] **六类内容覆盖**（完整性）：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白。
- [ ] **可核验**（可核验性）：每条结论在 `evidence_matrix` 有 locator+逐字 quote，检索式/纳排规则写入 `search_protocol`。
- [ ] **创新边界**（创新增益）：每条 novelty 有 `covered_by` 与 `verdict`，场景迁移/结论修正未当方法创新。
- [ ] **可行性前提有出处**（可行性）：每条 feasibility 标 `reported|assumed|missing|conflicting`，assumed 显式标注。
- [ ] **冲突不隐藏**（可靠体验）：相反证据进 `conflicts` 并说明取舍；"未报告"≠"不存在"。
- [ ] **诚实状态**（可靠体验）：status 与 `errors/degradations/pending_human/limitations` 一致，无编造。
- [ ] **骨架先行**（运行稳定性）：Phase 0 已在 3 分钟内落盘 result.json。
- [ ] **可复跑**（生态贡献）：同一输入两次运行结论一致；`change_log` 记录改动。

交付前运行自检脚本：

```bash
python3 -B scripts/main.py selftest        # 或 python3 -B scripts/selftest.py
python3 -B scripts/main.py --help          # 退出码 0
```

## 6. 降级与诚实性

- 缺数据：对应字段保持 `null/[]` 并写入 `uncertainty.gaps`，status 降级；**不编造数据、DOI、分数或运行结果**。
- 缺软件（如 `pdftotext` 不可用）：记录到 `degradations`，PDF 标 `text_unavailable`，仍继续处理其余文本材料。
- 冲突/偏倚/空白：全部登记到 `conflicts` 与 `uncertainty`；不确定的结论标为推断（`inferred`），不把推断写成事实。
- 写盘失败：脚本降级写最小合法结果，不让进程崩溃；每次运行退出码 0（finalize 校验出契约缺失时返回 2 以示需修正，但仍先写合法结果）。

## 7. 脚本与资源

- `scripts/main.py`：入口，子命令 `skeleton`/`evidence`/`finalize`/`selftest`；缺参数用缺省值，未知参数只告警。
- `scripts/core_impl.py`：鲁棒原语与各阶段实现（仅标准库）。
- `scripts/selftest.py`：独立自检（空输入/坏 JSON/缺参数/超大文件/坏编码）。
- `references/assessment-rules.md`：四项交付的判定口径与证据字段。
- `references/checklist.md`：可勾选自检与评分维度对照。

调用一律用相对路径 `scripts/*.py`；`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。
