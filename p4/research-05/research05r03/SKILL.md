---
name: sci-selection-review-verifiable
description: 科研选题与创新性评估 Skill。把固定研究问题拆成可检验陈述，对已有工作建立可比较证据矩阵，逐条判定创新点覆盖与可行性前提，所有关键结论回链到材料原文（locator+逐字 quote）。脚本管线保证同输入两次运行 result.json 字节一致，并用 scripts/selftest.py 自检。用于立项/投稿前选题评估、文献证据综述与边界说明。
version: 2.0.0
---

# 科研选题与创新性评估 Skill（自检与确定性变体）

## 1. 角色与何时使用

**角色**：立项/投稿前选题评估的执行者。只依据 `/input` 材料做判断，把结论落到可回查的证据位置，不替作者补造材料、不编造 DOI/数值/分数。

**何时使用**：给定研究问题 + 一组文献/材料，回答四件相连的事——问题是否可检验、已有工作做到哪一步、增量在哪里、现有条件能否做成。输出四项交付（问题定义、已有工作比较、创新点识别、可行性判断）加证据回链与冲突证据。

**不适用**：一次性脚本；纯聊天问答；材料为零仍要求给结论（此时返回 `insufficient_evidence`，不填示例答案）。

## 2. 输入契约

运行时固定输入位于 `/input`：

- `/input/case.json`：任务元信息、材料清单；可能含 `output_contract`（若有，其全部字段必须原样回填）。
- `/input/task.md`：任务说明与评分要点。
- 材料文件：PDF（`pdftotext` 抽字）、`.md/.txt/.json/.csv/.xml/.html`、Office 二进制（`docx/pptx/xlsx` 用 zipfile+XML 抽字）等，按扩展名识别，不假设固定文件名。

硬规则：

- 文本一律容错读取（`utf-8-sig`→`utf-8`→`latin-1`），坏 JSON/坏编码记录到 `errors/degradations`，不崩溃。
- 单文件超 2MB 跳过并记录；不因标签猜内容，**实际读文件**。
- `case.json` 缺失或输入目录缺失 → `input_error`；有输入但无可读材料 → `insufficient_evidence`。

## 3. 输出契约

必须产出 `/app/output/result.json`。顶层字段：

| 字段 | 要求 |
|---|---|
| `case_id` | 保留输入标识；缺省时按输入目录哈希回退并标 `unresolved-` |
| `status` | `ok / partial / insufficient_evidence / input_error`，由 `finalize` 按内容推导，不手写 |
| `output_contract` | 原样回显 case.json 的 output_contract；其每个键同时回填到顶层，缺值给 `null/[]`，不改名不删未知键；case.json 无此字段时按默认契约生成 |
| `research_question / search_protocol / prior_work / novelty / feasibility` | 四项交付 + 检索协议（见 assessment-rules.md） |
| `evidence_matrix` | 每条判断证据：`source_id / locator / quote(逐字) / field / supports / status` |
| `conflicts` | 相反证据登记：`topic / side_a / side_b / resolution` |
| `synthesis` | `conclusions / research_gaps / boundary_statement`（只到证据范围为止） |
| `sources / uncertainty / pending_human / errors / degradations / limitations / runtime / change_log` | 来源清单；偏倚/空白/无法确定项；待人工确认；错误/降级/限制；预算常量；幂等重建的改动记录 |

同时产出 `/app/output/report.md`（人类可读摘要）。证据记录必须含 `locator`（如 `materials/paper.txt` 行号或 `task.md#L3`）与逐字 `quote`；无引用信息写 `pending_human` 或 `not_reported`，不造 DOI/数值。

四项交付结构见 `references/assessment-rules.md`；`status=ok` 必须四项齐全且 `evidence_matrix` 非空，否则自动降为 `partial`。

## 4. 执行流程（总预算 900 s / 64 次模型请求 / 单响应 ≤16384 tokens）

**请求纪律**：一次读一段，不把整本 PDF 灌进一个响应；写完一项立即落盘，不反复重读同一文件。**前 3 分钟内必须先落盘 partial 骨架**，之后只做增量改进。

### Phase 0（0–3 min，必须最先执行）：骨架先行

```bash
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
```

写 `/app/output/result.json`（`status=partial`，全部契约字段就位、内容为 `null/[]`）与 `/app/work/input_manifest.json`。此后任何失败都至少有合法部分结果。

### Phase 1（3–6 min）：材料发现、PDF/Office 抽字

```bash
python3 -B scripts/main.py evidence --input /input --work /app/work --output /app/output
```

生成 `/app/work/material_inventory.json`，刷新 `sources`；无可用材料时 status 置 `insufficient_evidence`。随后读 `task.md` + `case.json`，确定四项交付是否全部要求。

### Phase 2（6–11 min）：四项交付（每项写成 fragment 落盘）

按顺序完成，每项写一个 JSON 对象到 `/app/work/fragments/`（文件名固定）：
`01_question.json`、`02_prior.json`、`03_novelty.json`、`04_feasibility.json`，
键名与 result.json 一致，只放该项内容；`00_meta.json` 可放 `search_protocol`。

1. **研究问题定义**：拆成 对象/变量/基线/预期/可证伪条件 → 可检验陈述；没给齐标 `status=partial/missing` 并列缺口。
2. **已有工作比较**：按 任务/方法/数据/评价/结论 建矩阵，标不可比原因；禁止跨文档串值。
3. **创新点识别**：区分 方法/数据/场景迁移/结论修正，逐条判覆盖，`verdict` 取 `novel|covered|partially_covered|unverifiable`，给理由+证据。
4. **可行性判断**：核查 数据/算力/实验/时间/人员 等前提，每条标 `reported|assumed|missing|conflicting`，输出条件结论与失败风险。

### Phase 3（11–14 min）：综合、冲突、不确定性

写 `05_synthesis.json`：`evidence_matrix`（关键判断回链 locator+quote）、`conflicts`（相反证据不隐藏，写取舍）、`synthesis`（结论+空白+边界）、`uncertainty`（偏倚/空白/无法确定）、`pending_human`、`limitations`。

### Phase 4（14–15 min）：合并、校验、收尾

```bash
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```

深合并 fragments、按内容推导 status、幂等重建 change_log、校验契约（缺字段/证据定位空/NaN），写最终 `result.json` 与 `report.md`。校验失败退出码 2。剩余时间只做增量修正（改写 fragment 后重跑 finalize）。

## 5. 自检清单（逐条对应评分维度）

- [ ] **可检验问题**：`testable_statement` 含对象/变量/基线/可证伪条件（任务完成度）。
- [ ] **四项交付齐全且一致**：问题、比较、创新、可行性引用同一证据集（正确性）。
- [ ] **六类内容覆盖**：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白（完整性）。
- [ ] **可核验**：关键结论在 `evidence_matrix` 有 locator+逐字 quote；`search_protocol` 有检索式与纳排规则（可核验性）。
- [ ] **创新边界**：每条 novelty 有 `covered_by + verdict`；场景迁移/结论修正未当方法创新（创新增益）。
- [ ] **可行性前提有出处**：每条标 `reported|assumed|missing|conflicting`，assumed 显式标注（可行性）。
- [ ] **冲突不隐藏**：相反证据进 `conflicts` 并说明取舍；"未报告"≠"不存在"（可靠体验）。
- [ ] **诚实状态**：status 与 `errors/degradations/pending_human/limitations` 一致（可靠体验）。
- [ ] **骨架先行**：Phase 0 已在 3 min 内落盘 result.json（运行稳定性）。
- [ ] **确定性**：同输入两次运行 result.json 字节一致，`scripts/selftest.py` 全 PASS（生态贡献）。

提交前跑：`python3 -B scripts/main.py selftest --skill . --input /input --output /app/output --work /app/work`。

## 6. 降级与诚实性

- 缺数据：对应字段保持 `null/[]`，写入 `uncertainty.gaps`，status 降级；**不编造数据、DOI、分数或运行结果**。
- 缺软件（`pdftotext` 不可用）：`evidence` 已在 `degradations` 记录，PDF 标 `text_unavailable`，继续处理其余文本材料。
- 冲突/偏倚/空白：全部登记到 `conflicts` 与 `uncertainty`；不确定的结论标 `inferred`，不写成事实。
- 写盘失败：脚本降级写最小合法结果，不让进程崩溃；每次运行退出码 0（finalize 校验失败除外，返回 2 以示需修正）。

## 7. 脚本与资源

- `scripts/main.py`：入口（skeleton/evidence/finalize/selftest 子命令）。
- `scripts/core_impl.py`：确定性实现层（材料发现、PDF/Office 抽字、契约回填、合并、校验、报告）。
- `scripts/selftest.py`：frontmatter + 契约 + 端到端确定性 + finalize 幂等 自检。
- `references/assessment-rules.md`：四项交付判定口径与证据字段。
- `references/checklist.md`：可勾选自检与评分维度对照。

全部脚本仅依赖 Python 标准库（`pdftotext` 为平台预装工具，缺失时降级）。调用一律用相对路径 `scripts/*.py`；`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。

## 8. 确定性设计（本变体取舍）

结构确定性由脚本保证：`result.json` 经 `canonical_dumps(allow_nan=False)` 写出（键排序、无 NaN、无挂钟时间戳），文件发现按相对路径排序，case_id 回退哈希只依赖相对路径+字节数+前 4KB 内容，`finalize` 幂等（change_log 重建而非追加）。语义确定性由固定口径约束：四项交付字段、verdict 取值、feasibility 状态、status 推导规则全部固定于 assessment-rules.md，模型自由文本只进规定的 prose 字段，结构由脚本装配。`selftest.py` 用"重跑骨架→证据→合并与既有 result.json 逐字节比对"验证该保证。
