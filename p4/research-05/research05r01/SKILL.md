---
name: research-topic-evidence-review
description: 科研选题与创新性评估 Skill（证据溯源强化版）。把固定研究问题拆成可检验表述，对已有工作建立可比较证据矩阵，逐条判定创新点覆盖与可行性前提；每条结论强制回链 locator+逐字 quote 并注册进 claims 型 evidence_map（单一事实源），冲突证据单独登记，最终产出可复核的 /app/output/result.json 与 report.md。用于立项/投稿前的选题评估与文献证据综述。
version: 1.1.0
---

# 科研选题与创新性评估 Skill（证据溯源强化版）

## 1. 角色与何时使用

**角色**：立项/投稿前的选题评估执行者。只依据 `/input` 材料做判断，每条结论都落到可回查的证据位置（locator+逐字 quote），不替作者补造材料、不把"检索不到"写成"尚无人研究"。

**何时使用**：给定一个研究问题 + 一组材料，需回答四件相连的事——问题可否检验、已有工作做到哪、增量在哪、现有条件能否做成，且每条结论可回查原文、相反证据不隐藏。

**不适用**：一次性脚本、纯聊天问答、材料为零却要结论（此时返回 `insufficient_evidence`）。

**本版取舍（证据溯源强化）**：证据升级为一等公民——四项交付与综合结论全部注册为 `evidence_map.claims`，每条 claim 强制带 locator+逐字 quote 与 `quote_status`；交付对象只引用 claim_id，不复制证据，形成单一事实源、可交叉校验。为此压缩综述篇幅，换取可复核性。

## 2. 输入契约

运行时固定输入位于 `/input`：

- `/input/case.json`：任务元信息、材料清单；可能含 `output_contract`（其全部字段必须原样回填并同步到顶层）。
- `/input/task.md`：任务说明与评分要点。
- 材料文件：PDF（用 `pdftotext` 抽文字）、`.md/.txt/.json/.csv/.xml/.html`、办公二进制等，按扩展名识别，不假设固定文件名；以实际文件内容为准。

硬规则：
- 文本容错读取（`utf-8-sig`→`utf-8`→`latin-1`）；坏 JSON/坏编码记入 `errors`/`degradations`，不崩溃。
- 单文件 >2MB 跳过并记录；`case.json` 缺失或 `/input` 缺失 → `input_error`；有输入但无可读材料 → `insufficient_evidence`。
- 实际读文件，不依据文件名/领域标签补造数据。

## 3. 输出契约

必须产出 `/app/output/result.json`。顶层字段：

| 字段 | 要求 |
|---|---|
| `case_id` | 保留输入标识；缺省时按输入目录哈希回退并标 `unresolved-` |
| `status` | `ok` / `partial` / `insufficient_evidence` / `input_error`，诚实取值 |
| `output_contract` | 原样回显 case.json 的 `output_contract`；其每个键同时回填到顶层，缺值给 `null`/`[]`，不改名不删未知键 |
| `research_question` / `search_protocol` / `prior_work` / `novelty` / `feasibility` | 四项交付（见第 4 节），只含内容并引用 claim_id |
| `evidence_map` | **本版核心**：`claims` 数组，每条 `claim_id/field/claim/kind/verdict/evidence[]/confidence/rationale` |
| `conflicts` | 相反证据登记：`topic`、两方记录、取舍理由 |
| `synthesis` | `conclusions`、`research_gaps`、`boundary_statement`（只到证据范围为止） |
| `sources` / `uncertainty` / `change_log` | 来源清单；偏倚/空白/无法确定项；按时间顺序的改动记录 |
| `pending_human` / `errors` / `degradations` / `runtime` | 待人工确认、错误、降级、运行统计 |

`evidence_map.claims[]` 字段规范：

- `claim_id`：稳定编号，前缀 `rq/sp/pw/nv/fb/syn/cf`（见 `references/assessment-rules.md`）；交付对象用 `claims:["rq-1"]` 引用。
- `claim`：一句断言；`kind`：`fact|inference|gap|conflict`；`field`：所属交付。
- `verdict`：novelty 取 `novel|covered|partially_covered|unverifiable`；feasibility 取 `supported|unsupported|conditional|unknown`；其余取 `established|inferred|unconfirmed`。
- `evidence[]`：每项 `source_id/locator/quote/relation/quote_status`；`quote` 逐字取自材料，`relation` 取 `supports|contradicts|contextualizes`，`quote_status` 取 `verified|inferred|missing|conflicting`。
- 找不到原文时 `quote_status=missing`、`quote=""`，不得编造；该 claim 同步进 `uncertainty.gaps` 与 `pending_human`。

同时产出 `/app/output/report.md`（人类可读摘要）。locator 示例：`materials/paper.txt#L10-L14`、`materials/paper.pdf#p3`、`task.md#L3`。无引用信息写 `not_reported`，不造 DOI/数值。

## 4. 执行流程（总预算 900 s / 64 次模型请求 / 单响应 ≤16384 tokens）

请求纪律：一次读一段，不把整本 PDF 灌进一个响应；写完一项立即落盘，不反复重读同一文件。**前 3 分钟必须先落盘一版结果骨架，之后只做增量改进。**

### Phase 0（0–3 min，必须最先执行）：落盘骨架

```bash
python3 -B scripts/skeleton.py --input /input --output /app/output --work /app/work
```

写 `/app/output/result.json`（status=partial/input_error，全部契约字段就位、内容为 `null/[]`）与 `/app/work/input_manifest.json`。此后任何失败都至少有合法部分结果。

### Phase 1（3–6 min）：发现材料、抽 PDF 文字

```bash
python3 -B scripts/evidence.py --input /input --work /app/work --output /app/output
```

生成 `/app/work/material_inventory.json` 与 `/app/work/evidence_index.jsonl`（可 grep 的 source_id+locator+原文），刷新 `sources`；无可读材料时 status 置 `insufficient_evidence`。随后读 `task.md` + `case.json`，确定四项交付是否全部要求。

### Phase 2（6–13 min）：四项交付（每项写成 fragment 落盘）

按顺序完成，每完成一项就写 JSON 到 `/app/work/fragments/`（`01_question.json`、`02_prior.json`、`03_novelty.json`、`04_feasibility.json`）。每个 fragment 含该项交付对象 + 其 `evidence_map.claims`：

1. **研究问题定义**：拆为 对象/关键变量/比较基线/预期结论/可证伪条件 → 写 `testable_statement`；缺项标 `status=partial` 并列 `gaps`。
2. **已有工作比较**：按 任务/方法/数据/评价方式/结论 建 `comparison_matrix`，标 `comparable` 与 `incomparable_reason`；禁止跨文档串值。
3. **创新点识别**：区分 方法/数据/场景迁移/结论修正，逐条 `verdict` 与 `covered_by`；场景迁移/结论修正不得当方法创新。
4. **可行性判断**：核查 数据/算力/实验条件/时间窗口/人员能力，每条标 `reported|assumed|missing|conflicting`，输出带条件结论与失败风险。

### Phase 3（13–15 min）：综合、冲突、不确定性

写 `05_synthesis.json`，补 `synthesis`（结论+研究空白+边界）、`conflicts`（相反证据不隐藏，写明取舍）、`uncertainty`（偏倚/空白/无法确定），并登记 `syn/cf` 前缀的 claims。

### Phase 4（15 min）：合并、校验、收尾

```bash
python3 -B scripts/finalize.py --input /input --output /app/output --work /app/work
```

深合并所有 fragments 到骨架，回查契约字段齐全、无 NaN、claim_id 引用闭合、每条证据有 locator+逐字 quote，写最终 `result.json` 与 `report.md`。退出码 0=通过，2=校验失败（据 `errors/pending_human` 修正后重跑）。剩余时间只做增量修正。

## 5. 自检清单（逐条可勾选，对应评分维度）

详见 `references/checklist.md`；核心对照：

- [ ] **任务完成度**：`testable_statement` 含对象/变量/基线/可证伪条件。
- [ ] **正确性**：四项交付齐全且引用同一证据集；创新点 `verdict`+`covered_by` 正确。
- [ ] **完整性**：覆盖问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白六类。
- [ ] **可核验**：每条结论在 `evidence_map` 有 locator+逐字 quote；检索式/纳排已写入 `search_protocol`。
- [ ] **创新增益**：场景迁移/结论修正未当方法创新；口径已写入 assessment-rules。
- [ ] **可行性**：每条前提有 `reported|assumed|missing|conflicting`，assumed 显式标注。
- [ ] **冲突不隐藏**：相反证据进 `conflicts`；"未报告"≠"不存在"。
- [ ] **诚实状态**：`status` 与 `errors/degradations/pending_human/limitations` 一致，无编造。
- [ ] **骨架先行**：Phase 0 已在 3 min 内落盘 result.json。
- [ ] **可复跑**：同一输入两次运行结论一致；`change_log` 记录改动。

提交前运行自检脚本：`python3 -B scripts/selftest.py`（应 4/4 passed）。

## 6. 降级策略与诚实性

- 缺数据：对应字段保持 `null/[]` 并写入 `uncertainty.gaps`，status 降级；**不编造数据、DOI、分数或运行结果**。
- 缺软件（如 `pdftotext` 不可用）：`evidence.py` 已在 `degradations` 记录，PDF 标 `text_unavailable`，继续处理其余文本材料。
- 冲突/偏倚/空白：全部登记 `conflicts` 与 `uncertainty`；不确定结论标 `inferred`，不把推断写成事实。
- 写盘失败：脚本降级写最小合法结果，不崩溃；运行退出码 0（finalize 校验失败返回 2 以示需修正）。
- 材料无法支撑结论时输出 `insufficient_evidence`，而不是示例填充。

## 7. 脚本与资源

- `scripts/skeleton.py`：骨架 + 契约回填（先跑，3 min 内）。
- `scripts/evidence.py`：材料发现 + PDF 抽字 + 证据索引 + sources 刷新。
- `scripts/finalize.py`：fragment 合并 + 契约/证据校验 + report.md。
- `scripts/selftest.py`：空输入/坏 JSON/全流程自检（合成临时数据，不写正式产物）。
- `references/assessment-rules.md`：四项交付判定口径与证据字段。
- `references/checklist.md`：可勾选自检与评分维度对照。

全部脚本仅依赖 Python 标准库（`pdftotext` 为平台预装工具，缺失时降级）。调用一律用相对路径 `scripts/*.py`；`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。
