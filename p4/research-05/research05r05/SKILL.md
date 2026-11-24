---
name: research-selection-evidence-review
description: 科研选题与创新性评估 Skill。把固定研究问题拆成可检验表述，对已有工作建立可比较证据矩阵，逐条判定创新点覆盖与可行性前提，所有结论回到材料 locator+逐字 quote，并逐字段对齐 case.json 的 output_contract、维护 change_log，输出可复核的 result.json 与 report.md。当需要做立项/投稿前选题评估、文献证据综述与边界说明时使用。
version: 2.0.0
---

# 科研选题与创新性评估 Skill

## 1. 角色与适用场景

本 Skill 是立项、投稿、项目申报前的选题评估执行者：只依据 `/input` 中的材料判断，把结论落到可回查的证据位置，不替作者补造材料。它把“研究问题定义、已有工作比较、创新点识别、可行性判断”当作同一判断的四个侧面，并额外输出检索协议与证据回链。

**何时使用**：给定一个研究问题 + 一组文献/材料，需要回答——问题是否可检验、已有工作做到哪一步、增量在哪里、现有条件能否做成。适用：文献综述、系统筛选、趋势分析与选题评估。

**不适用**：一次性脚本；纯聊天问答；材料为零却要求给出结论（返回 `insufficient_evidence`）。没有真实材料时不输出示例填充。

## 2. 输入契约

运行时固定输入位于 `/input`，最终产物写到 `/app/output`，中间态写到 `/app/work`：

- `/input/case.json`：任务元信息；可能含 `output_contract`（存在时其全部字段必须原样回显，并逐字段对齐到顶层）。
- `/input/task.md`：任务说明与评分要点。
- 材料文件：PDF（用 `pdftotext` 抽文字）、`.md/.txt/.json/.csv/.tsv/.xml/.html/.yaml/.tex/.bib` 等，按扩展名识别，不假设固定文件名。

硬规则：文本按 `utf-8-sig→utf-8→latin-1` 容错读取；坏 JSON/坏编码记入 `errors/degradations`，不崩溃；单文件超 2MB 跳过并记录；不凭标签猜内容，实际读文件。脚本调用一律相对路径 `scripts/*.py`，`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层字段：

| 字段 | 要求 |
|---|---|
| `case_id` | 保留输入标识；缺省按输入目录哈希回退并标 `unresolved-` |
| `status` | `ok / partial / insufficient_evidence / input_error`，诚实取值 |
| `output_contract` | 原样回显 case.json 的 `output_contract` |
| `contract_alignment` | 每个契约键的 `contract_value / top_level / filled / collides_with_reserved / source`，逐字段对齐 |
| 契约键 | 每个 `output_contract` 键同时回填到顶层，缺值给 `null/[]`，不改名不删未知键 |
| `research_question` / `search_protocol` / `prior_work` / `novelty` / `feasibility` | 四项交付 + 检索协议 |
| `evidence_matrix` | 每条判断：`source_id`、`locator`、`quote`（逐字）、`field`、`status` |
| `conflicts` | 相反证据：`topic`、两方记录、取舍理由 |
| `synthesis` | `conclusions`、`research_gaps`、`boundary_statement` |
| `sources` / `uncertainty` / `pending_human` / `errors` / `degradations` / `runtime` | 来源清单；偏倚/空白；待人工确认；错误；降级；运行统计 |
| `change_log` | 按时间顺序的改动记录（每条含 `at/actor/action/file/status/detail`） |

同时产出 `/app/output/report.md`（人类可读摘要，只反映 result.json 已有内容，不新增事实）。证据记录必须含 `locator`（如 `materials/note.md#L3` 或 PDF 抽取文本行号）与逐字 `quote`；无引用信息写 `pending_human` 或 `not_reported`，不造 DOI/数值。

## 4. 执行流程（总预算 900 s / 64 次模型请求 / 单响应 ≤16384 tokens）

请求纪律：一次读一段，不把整本材料灌进一个响应；写完一项立即落盘，不反复重读同一文件。

- **Phase 0（0–3 min，必须最先）**：落盘骨架。`python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`。写 `result.json`（`status=partial`，全部契约字段就位、内容 `null/[]`）与 `work/input_manifest.json`。此后任何失败都至少留下合法部分结果。
- **Phase 1（3–6 min）**：发现材料、抽 PDF 文字。`python3 -B scripts/main.py evidence --input /input --output /app/output --work /app/work`。生成 `work/material_inventory.json`，刷新 `sources`；无可用材料时把 `status` 置为 `insufficient_evidence`。随后读 `task.md` + `case.json`，确定四项交付是否全部要求。
- **Phase 2（6–11 min）**：四项交付，每完成一项写一个 fragment 到 `work/fragments/`（`01_question.json`、`02_prior.json`、`03_novelty.json`、`04_feasibility.json`），键名与 result.json 一致，只放该项内容；契约字段（如 `recommendation`）也在对应 fragment 中给出。
  1) 研究问题定义：拆成 研究对象、关键变量、比较基线、预期结论、可证伪条件 → 可检验陈述；缺项标 `status=partial/missing` 并列缺口。
  2) 已有工作比较：按 任务/方法/数据/评价方式/结论 建比较矩阵，标不可比原因，禁止跨文档串值。
  3) 创新点识别：区分 方法/数据/场景迁移/结论修正，逐条 `verdict ∈ novel|covered|partially_covered|unverifiable`，给理由+证据。
  4) 可行性判断：核查 数据/算力/实验条件/时间窗口/人员能力，每条标 `reported|assumed|missing|conflicting`，assumed 显式标注。
- **Phase 3（11–14 min）**：补 `evidence_matrix`、`conflicts`、`synthesis`、`uncertainty`（写成 `05_synthesis.json` 等 fragment）。
- **Phase 4（14–15 min）**：合并校验收尾。`python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work`。深合并 fragments，回查契约字段齐全、无 NaN、证据定位非空，写最终 `result.json` 与 `report.md`。剩余时间只做增量修正（追加 fragment 后重跑 finalize）。

若时间将尽仍未写完四项交付，重跑 `finalize` 即可得到诚实 `partial` 结果，不编造剩余内容。

## 5. 自检清单（对应评分维度，交付前逐条勾选）

- [ ] **可检验问题**：`research_question.testable_statement` 含对象/变量/基线/可证伪条件（任务完成度）。
- [ ] **四项交付齐全一致**：问题、比较、创新、可行性引用同一证据集（正确性）。
- [ ] **六类内容覆盖**：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白（完整性）。
- [ ] **可核验**：每条结论在 `evidence_matrix` 有 locator+逐字 quote；检索式/纳排规则写入 `search_protocol`。
- [ ] **创新边界**：每条 novelty 有 `covered_by` 与 `verdict`；场景迁移/结论修正未被当方法创新（创新增益）。
- [ ] **可行性前提有出处**：每条标 `reported|assumed|missing|conflicting`，assumed 显式标注。
- [ ] **契约对齐**：`contract_alignment` 中每个键 `top_level` 已填或显式 `null`，`change_log` 记录每次改动。
- [ ] **冲突不隐藏**：相反证据进 `conflicts` 并说明取舍；“未报告”≠“不存在”（可靠体验）。
- [ ] **诚实状态**：`status` 与 `errors/degradations/pending_human` 一致，无编造（可靠体验）。
- [ ] **骨架先行**：Phase 0 已在 3 min 内落盘 result.json（运行稳定性）。
- [ ] **可复跑**：同一输入两次运行结论一致；运行前可先跑 `python3 -B scripts/selftest.py`。

## 6. 降级策略（诚实性）

- 缺数据：对应字段保持 `null/[]` 并写入 `uncertainty.gaps`，`status` 降级；不编造数据、DOI、分数或运行结果。
- 缺软件（`pdftotext` 不可用）：`evidence` 阶段在 `degradations` 记录，PDF 标 `text_unavailable`，继续处理其余文本材料。
- 冲突/偏倚/空白：登记到 `conflicts` 与 `uncertainty`；不确定结论标 `inferred`，不把推断写成事实。
- 坏 JSON/坏编码/超 2MB：记录到 `errors/degradations`，不崩溃；写盘失败降级写最小合法结果，退出码 0。
- 空输入目录 → `input_error`；有输入但无可读材料 → `insufficient_evidence`，两者仍产出合法 result.json。

## 7. 脚本与资源

- `scripts/main.py`：子命令 `skeleton | evidence | finalize | run`；`--help` 退出码 0。
- `scripts/core_impl.py`：三阶段实现 + 契约对齐/NaN 校验/报告渲染（纯标准库）。
- `scripts/selftest.py`：自建合成夹具跑通管线并断言契约（不依赖真实案例）。
- `references/assessment-rules.md`：四项交付判定口径与证据字段规范。
- `references/checklist.md`：完整可勾选自检与评分维度对照。

正式运行：

```bash
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py evidence --input /input --output /app/output --work /app/work
# …… 写入 work/fragments/*.json ……
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```
