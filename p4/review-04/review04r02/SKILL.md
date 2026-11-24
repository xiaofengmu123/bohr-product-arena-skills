---
name: paper-argument-audit
description: 对科研论文/材料做"论点—证据—推理链"逻辑审查，产出带逐字定位、类型、严重度、可执行修改建议与证据映射的可复核问题清单。覆盖事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查项，区分事实错误、证据不足、规范问题与建议；禁止把对照组、明确假设、转述观点误判为缺陷。当需要逐条核查论文主张与证据、跨章节一致性、按严重度分级、给能落到具体句子的修改建议时使用。
version: 1.1.0
---

# 论文论点—证据—推理链审查 Skill

## 1. 角色与何时使用

你是论文逻辑与论证审稿人：**发现问题、给出证据、给出能落到具体句子的修改建议**，不是写摘要或读后感。

- 适用：对给定论文/科研材料完成一轮可交付审查，输出带原文位置、类型、严重度和修改建议的问题清单。
- 不适用：文字润色、查重/学术不端判定、参考文献格式、原始数据重算、投稿策略（题面明确排除）。

核心纪律：每条 finding 都必须**真实存在、能回到原文某一句话、建议能落到具体句子**。宁可少报、标 `pending_review`，不可编造。每个结论都要区分作者主张（author）、引用他人观点（cited）、假设情形（assumption）。

## 2. 输入契约

运行时原始输入在 `/input/`：

- `/input/case.json`：任务说明、`case_id`、可能的 `output_contract`、评分所需证据范围。
- `/input/task.md`：题目与交付要求。
- 材料在 `/input/materials/` 或 `/input/` 下（PDF、txt、md 等；PDF 抽取基于真实文件，无预抽取答案层）。**必须读真实文件内容，不依据文件名/领域标签臆测**。

产物写到 `/app/output/`；中间文件写到 `/app/work/`。执行前先读两个 references：`references/review-checklist.md`（六类清单与防误判守则）、`references/severity-rubric.md`（严重度/类型分级）。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段：

```json
{
  "case_id": "来自 case.json，缺省用文件名",
  "status": "ok | partial | insufficient_evidence | input_error",
  "task": {"challenge_id": "...", "task_title": "..."},
  "review": {
    "summary": "一段话总评",
    "findings": [{"id":"F001","type":"overclaim","severity":"major","locator":"p3¶7","quote":"逐字原文","claim":"被核查主张","evidence":"判断依据","reasoning":"三问：影响主结论/可修正/可复现","suggestion":"落到具体句子的修改","status":"confirmed|probable|pending_review|ruled_out","confidence":"high|medium|low"}],
    "claims": [{"id":"C001","text":"主张原文","speaker":"author|cited|assumption","section":"...","locator":"...","verdict":"supported|overclaimed|unsupported|pending","evidence":"..."}],
    "commitments": [{"id":"...","promise":"引言承诺原文","locator":"...","delivered_at":"结果/讨论位置或 null"}],
    "coverage": {"factual_verification":{"status":"done|partial|skipped","checked":0,"note":""},"citation_support":{...},"method_design":{...},"statistical_reporting":{...},"argumentation_chain":{...},"academic_norms":{...}},
    "section_map": [{"section":"...","pages":[1]}]
  },
  "sources": [{"path":"/input/...","kind":"task|material","n_pages":0,"chars":0}],
  "uncertainty": [], "pending_review": [], "pending_human": [], "limitations": [], "errors": [], "degradations": [],
  "change_log": [{"ts":"...","event":"scaffold_written|incremental_update|finalize","note":"..."}],
  "runtime": {"seconds_elapsed":0.0,"model_requests_used":0,"budget":{"requests":64,"seconds":900}}
}
```

- **必须原样回填 case.json 里 `output_contract` 的全部键**：缺值给 `null`/`[]`，不改名、不删未知键（`main.py scaffold` 自动处理）。
- 另产出 `/app/output/report.md`（人类可读报告，与 result.json 同步，由脚本从 result.json 生成，避免漂移）。Office/图类产物（本题通常不需要）如产出需附真实渲染的 PDF/PNG 预览。
- `status` 诚实：材料不足即 `insufficient_evidence`，不编数据或结论。`coverage` 六维必须逐维标注是否检查、检查了多少条。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**时间优先：前 3 分钟必须落盘一版合法 partial 骨架，之后只做增量改进。** 阶段硬预算如下，超时即停并如实写 `limitations`：

| 阶段 | 预算秒 | 预算请求 | 交付物 |
|---|---|---|---|
| P0 scaffold | ≤150 | ≤3 | 合法 partial 骨架 + corpus.md |
| P1 主张抽取 | ≤150 | ≤12 | claims[]/commitments[]（分 speaker） |
| P2 六维审查 | ≤300 | ≤30 | 六维 findings[]（逐条定位+证据+严重度） |
| P3 跨章节一致性 | ≤120 | ≤10 | 承诺—交付对照结论 |
| P4 终检收尾 | ≤180 | ≤9 | selfcheck 通过 + 诚实 status |

1. **P0 脚手架（前 3 分钟内，约 1 次请求）**：读两个 references，执行
   `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`
   产出 `work/corpus.md`（带 `p页¶段` 或 `L段` 定位的逐段语料）、`work/meta.json`、`work/budget.json`、`result.json` 与 `report.md` 骨架。**这一版必须已合法可解析**，任何后续异常都不留半截产物。
2. **P1 通读与主张抽取（约 5–10 次请求）**：读 `work/corpus.md`，按 checklist 第 0/8 节建主张清单，区分 speaker，登记 `claims[]` 与 `commitments[]`。
3. **P2 六维逐项审查（约 20–28 次请求）**：按 `references/review-checklist.md` 六维逐段核查，每条 finding 写 `locator + quote + claim + evidence + reasoning + suggestion`，套用 `references/severity-rubric.md` 定 type/severity/status/confidence。六维都要实际查，不可只查一维就标"完成"。
4. **P3 跨章节一致性（约 6–8 次请求）**：单独做"承诺—交付"对照，检查引言承诺、摘要与正文、方法与结果口径、图表与正文是否一致。
5. **增量写回（每攒够一批即执行，避免丢失；至少每 5 分钟一次）**：把 findings/claims/commitments/coverage 写入 `work/findings.json`、`work/claims.json` 等 JSON 数组/对象，执行
   `python3 -B scripts/main.py update --output /app/output --work /app/work --findings /app/work/findings.json --claims /app/work/claims.json --coverage /app/work/coverage.json --summary "..." --status partial`
   脚本会规范化、校验、记 change_log，并重新生成 report.md。可用 `python3 -B scripts/main.py budget --work /app/work` 查看各阶段预算使用。
6. **P4 终检（最后 2–3 次请求）**：执行 `python3 -B scripts/main.py selfcheck --output /app/output`，修复报出的问题；核对六维 `coverage` 都已标注、`limitations`/`pending_review` 齐全、无编造项，再以 `--status ok|partial|insufficient_evidence` 收尾。

时间纪律：第 1 步必须在前 3 分钟落盘；每 5 分钟写回一次；时间不足时宁可保持 `partial` 并写清 `limitations`，也不宣称完成全文审查。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] 真在审查而非摘要：每条 finding 有 `locator`、逐字 `quote`、`claim`、`evidence`、`reasoning`、可执行 `suggestion`。（实际效果·任务完成度/可核验性）
- [ ] 六类检查项 `coverage` 全标注，且确实做了跨章节"承诺—交付"对照。（实际效果·完整性）
- [ ] 没有把对照组、明确假设、转述观点误判为缺陷；speaker 已区分。（实际效果·正确性）
- [ ] 严重度有区分且 `reasoning` 写了三问（影响主结论/可修正/可复现），不是一律"中"。（创新增益·可解释分级）
- [ ] `result.json` 可解析，含 `case_id` + output_contract 全部字段 + `status/sources/uncertainty/change_log`。（可靠体验·稳定性）
- [ ] 空输入/坏 JSON/坏编码/PDF 抽取失败均有降级且记录 `errors/degradations`，不崩溃。（可靠体验·异常处理）
- [ ] 长文截断、章节缺失、无法外部核验的引用均已写入 `limitations`/`pending_review`，未宣称全文已查。（可靠体验）
- [ ] 建议都能落到具体句子（补什么证据、改哪句、如何降级结论强度）。（使用增益·减少人工重写）
- [ ] `selfcheck` 通过；重复运行结果确定（脚本无随机性）。（使用增益·可验证性）

## 6. 降级策略（缺数据/缺软件时如实标注，不编造）

- 缺材料、无文本层、PDF 抽取失败：先试平台 PDF/OCR 工具；仍失败则 `status=insufficient_evidence`，`limitations` 写清缺什么、影响哪些检查项，**不编造**。
- 引用无法获取全文：该条标 `pending_review`，写"需外部核对"，不得断言"引用不支持"。
- 缺 `pdftotext`：脚本自动降级并在 `degradations` 记录，Agent 改用平台 pdf-text-tool 后把文本补进 `work/corpus.md`。
- 参数缺失：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 写盘失败：脚本用临时文件原子替换；仍失败则输出最小合法 result.json，不让进程崩溃。
- 任何未确认项归入 `uncertainty` 或 `pending_review`/`pending_human`；绝不伪造 DOI、数值、引用或软件状态。

## 7. 最小调用示例（示意，非正式案例结果）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 work/corpus.md，产出 findings.json / claims.json / coverage.json 后：
python3 -B scripts/main.py update --output /app/output --work /app/work \
  --findings /app/work/findings.json --claims /app/work/claims.json \
  --coverage /app/work/coverage.json --status partial
python3 -B scripts/main.py selfcheck --output /app/output
python3 -B scripts/selftest.py   # 交付前自检：正常/坏输入/空输入三场景
```

示意输出（标为示例，勿冒充正式案例）：`selfcheck` 打印 `OK`；`result.json` 含上述全部字段。正式案例、白板与测评由平台提供，本 Skill 不预置任何答案。
