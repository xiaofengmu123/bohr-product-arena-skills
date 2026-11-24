---
name: sci-research-selection-review
description: 科研选题与创新性评估 Skill。以结构化中间产物（plan.json / evidence_index.json / evidence_model.json / figure_model.json / synthesis.json）驱动，把固定研究问题拆成可检验表述、对已有工作建立可比较证据矩阵、逐条判定创新点覆盖与可行性前提、登记冲突证据；每条结论回到材料 locator + 逐字 quote，输出可复核的 result.json 与 report.md。用于立项/投稿/申报前的选题评估、文献证据综述与边界说明。
version: 2.0.0
---

# 科研选题与创新性评估 Skill（中间表示与可复核版）

## 1. 角色与适用场景

**角色**：立项/投稿/申报前的选题评估执行者。只依据 `/input` 中的材料判断，把结论落到可回查的证据位置，不替作者补造材料，不编造 DOI、数值、分数或运行结果。

**何时使用**：给定一个研究问题 + 一组文献/材料，需回答四件相连的事——问题如何表述才可检验、已有工作做到哪一步、增量在哪里、现有条件能否做成，并附证据回链、冲突证据与边界说明。

**不适用**：一次性脚本；纯聊天问答；材料为零却要求给结论（此时返回 `insufficient_evidence`）。

**本版设计取舍**：结果不是一次性写成的，而是由五个结构化中间产物（见 §4）确定性合并而来。每条结论都能沿 `result.json → evidence_matrix → locator+quote → 材料` 独立复核；新增/修改只改对应中间产物，`finalize` 幂等重建结果。

## 2. 输入契约

运行时固定输入位于 `/input`：

- `/input/case.json`：任务元信息、材料清单；若含 `output_contract`，其全部字段必须原样回填到结果顶层与 `output_contract` 键，不改名、不删未知键，缺值给 `null`/`[]`。
- `/input/task.md`：任务说明与评分要点。
- 材料文件：PDF（用 `pdftotext` 抽文字）、`.md/.txt/.json/.csv/.xml/.html/.yaml/.bib` 等，按扩展名识别，不假设固定文件名，不按标签猜内容。

硬规则：文本按 `utf-8-sig → utf-8 → latin-1` 容错读取；坏 JSON/坏编码记入 `errors/degradations`，不崩溃；单文件超 2MB 跳过并记录；`case.json` 缺失或输入目录缺失 → `input_error`；有输入但无可读材料 → `insufficient_evidence`。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`。`result.json` 顶层字段：

| 字段 | 要求 |
|---|---|
| `case_id` | 保留输入标识；缺省按输入目录哈希回退并标 `unresolved-` |
| `status` | `ok / partial / insufficient_evidence / input_error`，诚实取值 |
| `output_contract` | 原样回显 case.json 的 output_contract；其每个键同时回填顶层 |
| `research_question` / `search_protocol` / `prior_work` / `novelty` / `feasibility` | 四项交付（§5） |
| `evidence_matrix` | 每条判断：`evidence_id/source_id/locator/quote(逐字)/claim/field/status` |
| `conflicts` | 相反证据：`topic`、两方记录、取舍理由 |
| `synthesis` | `conclusions / research_gaps / boundary_statement` |
| `sources` / `uncertainty` / `pending_human` / `errors` / `degradations` / `limitations` / `change_log` / `runtime` | 来源清单；偏倚/空白/无法确定项；待人工确认；错误/降级/限制；按时间顺序的改动记录；运行统计 |

`report.md` 为人类可读摘要。证据记录必须含 `locator`（如 `materials/paper.pdf` 对应抽取文本的行号，或 `task.md#L3`）与逐字 `quote`；无引用信息写 `pending_human` 或 `not_reported`，不造 DOI/数值。

## 4. 中间产物（可复核的关键）

Agent 在 `/app/work` 维护五个中间产物，`finalize` 只做确定性合并，不做智能判断：

1. `plan.json`：`research_question`（对象/变量/基线/预期/可证伪条件）、`search_protocol`（检索式/纳排/筛选步骤）、比较维度、创新类型、可行性维度、预算。
2. `evidence_index.json`：材料清单（`source_id/path/cite_path/text_available/char_count`），由 `discover` 生成。
3. `evidence_model.json`：`records[]`，每条含 `evidence_id/source_id/locator/quote/claim/field/status`。
4. `figure_model.json`：`comparison_matrix[]`（任务/方法/数据/评价/结论）、`novelty_claims[]`（`type`+`verdict`+`covered_by`）、`feasibility_premises[]`（`aspect`+`status`+`risk`）。
5. `synthesis.json`：`conclusions/research_gaps/boundary_statement/conflicts/uncertainty/pending_human/limitations`。

字段枚举见 `references/assessment-rules.md`；`record` 命令按 `evidence_id` 去重合并记录。

## 5. 执行流程（总预算 900 s / 64 次模型请求 / 单响应 ≤16384 tokens）

**请求纪律**：一次读一段，不把整本 PDF 灌进一个响应；写完一项立即落盘，不反复重读同一文件。

### Phase 0（0–3 分钟，必须最先执行）：先落盘骨架

```bash
python3 -B scripts/main.py init
```

写入合法 `result.json`（`status=partial`，契约字段齐全、内容为 `null/[]`）与全部中间产物骨架。此后任何失败都至少有合法部分结果。

### Phase 1（3–6 分钟）：发现材料、抽 PDF 文字

```bash
python3 -B scripts/main.py discover
```

生成 `evidence_index.json`，刷新 `sources`；无可用材料时把 `status` 置为 `insufficient_evidence`。随后读 `task.md` + `case.json`，确定四项交付是否全部要求。

### Phase 2（6–11 分钟）：写四项中间产物，逐项落盘

按序完成，每完成一项就把 JSON 写到 `/app/work/fragments/`（如 `01_plan.json`）并执行
`python3 -B scripts/main.py record --kind <plan|evidence|figure|synthesis> --file <path>`：

1. **研究问题定义**：拆成研究对象、关键变量、比较基线、预期结论、可证伪条件 → 写出可检验陈述；缺项标 `status=partial/missing` 并列缺口。
2. **已有工作比较**：按任务/方法/数据/评价方式/结论建比较矩阵，识别真正相近工作，标出不可直接比较原因；禁止跨文档串值。
3. **创新点识别**：区分方法创新/数据创新/场景迁移/结论修正，逐条判覆盖，`verdict` 取 `novel|covered|partially_covered|unverifiable`，给理由+证据；场景迁移与结论修正不得当方法创新。
4. **可行性判断**：核查数据/算力/实验条件/时间窗口/人员能力；每前提标 `reported|assumed|missing|conflicting`，`assumed` 显式标注，输出带条件结论与失败风险。

### Phase 3（11–14 分钟）：综合、冲突、不确定性

写 `04_synthesis.json`：`conclusions/research_gaps/boundary_statement`、`conflicts`（相反证据不隐藏，写明取舍）、`uncertainty`（偏倚/空白/无法确定项）。

### Phase 4（14–15 分钟）：合并、校验、收尾

```bash
python3 -B scripts/main.py finalize
python3 -B scripts/selftest.py
```

`finalize` 深合并所有中间产物到骨架，校验契约字段齐全、无 NaN、证据定位非空，写最终 `result.json` 与 `report.md`；校验失败返回 2。剩余时间只做增量修正（改某个 fragment 后重跑 `record` + `finalize`）。

## 6. 自检清单（提交/交付前，对应评分维度）

- [ ] **可检验问题**：`research_question.testable_statement` 含对象/变量/基线/可证伪条件（任务完成度）。
- [ ] **四项交付齐全且一致**：问题、比较、创新、可行性引用同一证据集（正确性）。
- [ ] **六类内容覆盖**：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白（完整性）。
- [ ] **可核验**：每条关键结论在 `evidence_matrix` 有 locator+逐字 quote；检索式/纳排规则已写入 `search_protocol`（可核验性）。
- [ ] **创新边界**：每条 novelty 有 `type`、`verdict`、`covered_by`；场景迁移/结论修正未误判为方法创新（创新增益）。
- [ ] **可行性前提有出处**：每条 feasibility 标 `reported|assumed|missing|conflicting`，assumed 显式标注（可行性）。
- [ ] **冲突不隐藏**：相反证据进 `conflicts` 并说明取舍；"未报告"≠"不存在"（可靠体验）。
- [ ] **诚实状态**：`status` 与 `errors/degradations/pending_human/limitations` 一致，无编造（可靠体验）。
- [ ] **骨架先行**：Phase 0 已在 3 分钟内落盘 result.json（运行稳定性）。
- [ ] **可复跑**：同一输入两次运行结论一致；`change_log` 记录每一步改动（生态贡献）。
- [ ] **脚本健康**：`python3 -B scripts/main.py --help`、`python3 -B scripts/selftest.py` 均退出码 0。

## 7. 降级与诚实性

- 缺数据：对应字段保持 `null/[]` 并写入 `uncertainty.gaps`，status 降级；不编造数据、DOI、分数或运行结果。
- 缺软件（如 `pdftotext` 不可用）：`discover` 在 `degradations` 记录，PDF 标 `text_unavailable`，仍继续处理其余文本材料。
- 冲突/偏倚/空白：全部登记到 `conflicts` 与 `uncertainty`；不确定结论标推断（`inferred`），不把推断写成事实。
- 区分三层证据状态：材料事实（`reported`）、合理推断（`inferred`）、无法确定（`missing`/`unverifiable`）；"检索不到"不得写成"尚无人研究"。
- 写盘失败：脚本降级写最小合法结果，不让进程崩溃；每次运行退出码 0（finalize 校验失败除外，返回 2）。

## 8. 脚本与资源

- `scripts/main.py`：入口，子命令 `init / discover / record / finalize`（先看 `--help`）。
- `scripts/core_impl.py`：机械实现（发现、抽字、合并、校验、report）。
- `scripts/selftest.py`：自检，构造合成输入验证契约与降级路径，退出码 0 为通过。
- `references/assessment-rules.md`：四项交付判定口径与字段枚举。
- `references/checklist.md`：可勾选自检与评分维度对照。

全部脚本仅依赖 Python 标准库（`pdftotext` 为平台预装工具，缺失时降级）。调用一律用相对路径 `scripts/*.py`；`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。
