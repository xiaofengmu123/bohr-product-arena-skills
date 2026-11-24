---
name: paper-audit-ledger
description: 对科研论文/材料做"逻辑与论证审查"，采用"审计台账"中间表示：先抽主张(claims)并区分 speaker，再派生问题(findings 必须引用 claim_id)，强制主张—证据逐条对齐。产出带精确 locator、逐字 quote、类型、可解释严重度与可执行修改建议的可复核报告，覆盖事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查项。用于论文质量审查；禁止把对照组、明确假设、转述观点误判为缺陷。当需要逐条审查主张与证据、跨章节一致性、严重度分级时使用。
version: 1.0.0
---

# 论文逻辑与论证审查 Skill（audit-ledger 版）

## 1. 角色与何时使用

你是论文逻辑与论证审稿人：任务是**发现问题、给出证据、给出可执行修改建议**，不是写摘要或读后感。

适用：对给定论文/科研材料完成一轮可交付审查，输出问题清单（每条含定位、类型、严重度、建议）与主张清单。

不适用（题目明确排除）：文字润色、查重/学术不端判定、参考文献格式检查、原始数据重算、投稿策略。

核心目标：每条 finding 都**真实存在、能回到原文某一句、建议能落到具体句子**。宁可少报、标 `pending_review`，不可编造。每个结论区分作者主张（author）、引用他人观点（cited）、假设情形（assumption）。

## 2. 输入契约

运行时原始输入在 `/input/`：

- `/input/case.json`：任务说明、`case_id`、可能的 `output_contract`。
- `/input/task.md`：题目与交付要求。
- 材料在 `/input/materials/` 或 `/input/`（PDF/txt/md/tex 等）。PDF 抽取基于真实文件，无预抽取答案层。**必须读真实内容，不依据文件名/领域标签臆测**。

产物统一写 `/app/output/`；中间文件写 `/app/work/`。脚本路径相对本 SKILL.md：`scripts/main.py`（入口）、`scripts/reviewkit.py`（核心库）、`scripts/selftest.py`（自检）。执行前先读 `references/checklist.md`（六类清单与防误判守则）、`references/rubric.md`（类型/严重度/状态分级）。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`（人类可读，与 result.json 同步）。

result.json 顶层必须含 `case_id`、`status`、`audit`、`sources`、`uncertainty`、`pending_human`、`change_log`、`runtime`；**另须原样回填 case.json 里 `output_contract` 的全部键**（缺值给 `null`/`[]`，不改名、不删未知键；`scaffold` 自动处理）。

- `status` 取 `ok | partial | insufficient_evidence | input_error`，须诚实：材料不足即 `insufficient_evidence`，不编数据或结论。
- `audit`：`summary` 总评；`claims[]`（id/text/speaker/section/locator/quote/verdict/evidence）；`commitments[]`（id/promise/promise_locator/delivered_locator/delivered）；`findings[]`（id/claim_id/type/severity/locator/quote/claim/evidence/reasoning/suggestion/status/confidence）；`coverage` 六维（每维 status/checked/note）；`section_map[]`。
- `findings[].claim_id` 必须引用 `claims[].id`（未引用时脚本会报警并写入 `degradations`）。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单次 ≤16384 tokens）

时间优先：先落盘骨架，再增量改进。每步标注输入/输出。审计台账 `work/ledger.json` 是唯一中间表示。

1. **脚手架（前 3 分钟内完成，约 1 次请求）**：读 `references/` 两个文件后执行
   `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`
   输入 `/input`；输出 `work/corpus.md`（带 `p页 ¶P段落` 标记的逐段语料）、`work/ledger.json`（空台账）、`/app/output/result.json` 骨架、`/app/output/report.md` 骨架。**这一版必须合法可解析**；任何后续异常都不留半截产物。
2. **通读与主张抽取（约 5–8 次请求）**：读 `work/corpus.md`，按 checklist 第 0/8 节建主张清单并区分 speaker，登记 `claims[]` 与 `commitments[]`（引言承诺）。
3. **六维逐项审查（约 20–30 次请求）**：按 checklist 六维逐段核查，每条 finding 写 `locator + quote + evidence + reasoning + suggestion`，套用 rubric 定 type/severity/status/confidence；**每条 finding 的 claim_id 必须指向某条 claim**。跨章节"承诺—交付"单独做一遍对照。
4. **增量合并（每攒够一批即执行，避免丢失）**：把 claims/findings/coverage/limitations 写入 `work/ledger.json`（结构见脚本 `empty_ledger()`），执行
   `python3 -B scripts/main.py merge --output /app/output --work /app/work`
   输入 ledger；输出更新后的 result.json + report.md（脚本规范化、交叉引用校验、记 change_log、原子写，防止整份 JSON 手写出错）。**至少每 5 分钟 merge 一次**。
5. **终检与自检（最后 2–3 次请求）**：执行 `python3 -B scripts/main.py selfcheck --output /app/output`，修复报出的问题；核对六维 `coverage` 已标注、`limitations`/`pending_review`/`uncertainty` 齐全、无编造项，再以 `merge` 写入最终 `status`。时间不足则保持 `partial` 并如实写 `limitations`，不宣称完成全文审查。

## 5. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] 真在审查而非摘要：每条 finding 有 locator、逐字 quote、evidence、reasoning、可执行 suggestion。（实际效果·可核验性）
- [ ] 六类检查项 `coverage` 全标注（done/partial/skipped 逐维），且做了跨章节"承诺—交付"对照。（实际效果·完整性）
- [ ] 未把对照组、明确假设、转述观点误判为缺陷；speaker 已区分。（实际效果·正确性）
- [ ] 严重度有区分且 reasoning 写了三问（影响主结论/可修正/可复现），不是一律"中"。（创新增益·可解释分级）
- [ ] 每条 finding 的 claim_id 指向已登记 claim，主张—证据逐条对齐。（创新增益·方法改进）
- [ ] result.json 可解析，含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log。（可靠体验·稳定性）
- [ ] 空输入/坏 JSON/坏编码/PDF 抽取失败均降级且记录 errors/degradations，不崩溃。（可靠体验·异常处理）
- [ ] 长文截断、章节缺失、无法外部核验的引用均已写入 limitations/pending_review，未宣称全文已查。（可靠体验）
- [ ] 建议能落到具体句子（补什么证据、改哪句、如何降级结论强度）。（使用增益·减少人工重写）
- [ ] `selftest.py` 与 `selfcheck` 通过；重复运行结果确定（除时间戳外脚本无随机性）。（使用增益·可验证性）

## 6. 降级策略（缺数据/缺软件时）

- 缺材料、无文本层、PDF 抽取失败：先试 `pdftotext`，失败则试 `.parser/<stem>.jsonl` 预抽取层；仍失败则 `status=insufficient_evidence`，`limitations` 写清缺什么、影响哪些检查项，**不编造**。
- 引用无法获取全文：该条标 `pending_review`，写"需外部核对"，不得断言"引用不支持"。
- 坏 JSON/坏编码：脚本容错并记录到 `errors`/`degradations`，继续处理其余材料。
- 缺 `pdftotext`：脚本自动降级并在 `degradations` 记录。
- 参数缺失：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 写盘失败：临时文件原子替换；仍失败则输出最小合法 result.json，不让进程崩溃。
- 任何未确认项归入 `uncertainty` 或 `pending_review`；绝不伪造 DOI、数值、引用或软件状态。

## 7. 最小调用示例（示意，非正式案例结果）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 work/corpus.md，填 work/ledger.json 后：
python3 -B scripts/main.py merge --output /app/output --work /app/work
python3 -B scripts/main.py selfcheck --output /app/output
python3 -B scripts/selftest.py
```

示意输出（标为示例）：`selfcheck` 打印 `OK`；`selftest` 打印 `selftest PASS (0 failed)`；result.json 含上述全部字段。正式案例、白板与测评由平台提供，本 Skill 不预置任何答案。

## 8. 架构说明（与单文件 scaffold/update 型的差异）

本包以"审计台账 ledger.json"为唯一中间表示：主张优先、findings 强制引用 claim_id，从数据层强制主张—证据对齐并抑制误报；校验顺序固定为"规范化 ledger → 交叉引用 → 生成产物 → schema 自检"，且所有写入原子化、可重跑。脚本仅依赖 Python 标准库。
