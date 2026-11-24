---
name: paper-logic-audit
description: 对科研论文/材料做"逻辑与论证审查"，产出带精确定位、类型、严重度、结构化三问与可执行修改建议的可复核报告。覆盖事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查项与跨章节"承诺—交付"对照；区分事实错误、证据不足、规范问题与建议性意见，禁止把对照组、明确假设、转述观点误判为缺陷。内置确定性脚手架、增量写回与 selfcheck/selftest，保证同输入同路径两次产物一致、异常可降级。当需要逐条审查论文主张与证据、跨章节一致性、严重度分级并留下可复核记录时使用。
version: 1.0.0
---

# 论文逻辑与论证审查 Skill

## 1. 角色与何时使用

你是一名论文逻辑与论证审稿人，任务是**发现问题、给出证据、给出可执行的修改建议**，不是写摘要或读后感。适用：对给定论文/科研材料完成一轮可交付审查，输出带原文位置、类型、严重度和修改建议的问题清单。不适用：文字润色、查重/学术不端判定、参考文献格式检查、原始数据重算、投稿策略（题面明确排除）。

核心目标：每条问题都**真实存在、能回到原文某一句话、建议能落到具体句子**。宁可少报、标 `pending_review`，不可编造。每个结论都要区分作者主张（author）、转述他人观点（cited）、假设情形（assumption）。

## 2. 输入契约

运行时原始输入在 `/input/`：

- `/input/case.json`：任务说明、`case_id`、可能的 `output_contract`。
- `/input/task.md`：题目与交付要求。
- 材料在 `/input/` 或 `/input/materials/` 下（PDF、txt、md 等）。**必须读真实文件内容，不依据文件名/领域标签臆测**；PDF 抽取基于真实文件，无预抽取答案层。

产物统一写到 `/app/output/`（含 `result.json`）；中间文件写到 `/app/work/`。脚本路径相对本 SKILL.md：`scripts/main.py`、`scripts/review_core.py`、`scripts/selftest.py`；参考资料：`references/review-checklist.md`（六类清单与防误判守则）、`references/severity-rubric.md`（严重度与建议模板）。执行前先读这两个 references。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`（两者同步）。result.json 字段：

```json
{
  "case_id": "来自 case.json，缺省用文件名",
  "status": "ok | partial | insufficient_evidence | input_error",
  "task": {"challenge_id": "...", "task_title": "..."},
  "review": {
    "summary": "一段话总评",
    "findings": [{"id":"F001","type":"overclaim","severity":"major","source":"/input/...","locator":"p3 ¶P0012","quote":"逐字原文","claim":"被核查的主张","evidence":"判断依据","reasoning":{"affects_main_conclusion":true,"fixable":true,"affects_reproducibility":false,"note":"..."},"suggestion":"落到具体句子的修改","status":"confirmed","confidence":"high"}],
    "claims": [{"id":"C001","source":"...","locator":"...","text":"主张原文","speaker":"author|cited|assumption","section":"...","verdict":"supported|overclaimed|unsupported|pending","evidence":"..."}],
    "commitments": [{"id":"M001","source":"...","locator":"...","promise":"引言承诺原文","delivered_at":"结果/讨论定位符或 null"}],
    "coverage": {"factual_verification":{"status":"done|partial|skipped","checked":0,"note":""}, "...其余五维同构"},
    "section_map": [{"section":"...","kind":"heading|paragraph","locator":"...","source":"..."}]
  },
  "artifacts": [{"path":"/app/output/result.json","kind":"result"}],
  "sources": [{"path":"/input/...","kind":"pdf|text","n_pages":0,"chars":0,"error":null}],
  "uncertainty": [], "pending_review": [], "pending_human": [], "limitations": [],
  "errors": [], "degradations": [],
  "change_log": [{"ts":"...","event":"skeleton_written|incremental_update","note":"..."}],
  "runtime": {"seconds_elapsed":0.0,"model_requests_used":0,"budget":{"requests":64,"seconds":900}}
}
```

- **必须原样回填 case.json 里 `output_contract` 的全部键**：缺值给 `null`/`[]`，不改名、不删未知键（`main.py scaffold` 自动处理）。
- 六类 coverage 维度键固定为：`factual_verification / citation_support / method_design / statistical_reporting / argument_chain / academic_norm`。
- `status` 诚实：材料不足即 `insufficient_evidence`，不编造数据或结论；`coverage` 六维必须逐维标注是否检查、检查了多少条。
- `report.md` 由 `main.py` 从 result.json 确定性渲染，不要手写以免不同步。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

时间优先，先落盘骨架再增量改进；每步标注输入/输出。

1. **脚手架（前 3 分钟内完成，约 1 次请求）**：读两个 references，然后执行
   `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`
   输入 `/input`；输出 `work/corpus.md`（带 `p页 ¶P段落` 标记的逐段语料）、`work/meta.json`、`/app/output/result.json` 骨架、`/app/output/report.md` 骨架。**这一版必须已合法可解析**，任何后续异常都不留半截产物。
2. **通读与主张抽取（约 5–8 次请求）**：读 `work/corpus.md`，按 checklist 第 0/8 节建主张清单，区分 speaker，登记 `claims[]` 与 `commitments[]`（引言承诺）。
3. **六维逐项审查（约 20–30 次请求）**：按 `references/review-checklist.md` 六维逐段核查；每条 finding 写 `locator + quote + evidence + reasoning(三问) + suggestion`，套用 `references/severity-rubric.md` 定 type/severity/status/confidence。跨章节"承诺—交付"对照单独做一遍。
4. **增量写回（每攒够一批即执行，避免丢失）**：把 findings/claims 写入 `/app/work/findings.json`、`/app/work/claims.json`（JSON 数组），执行
   `python3 -B scripts/main.py update --output /app/output --work /app/work --findings /app/work/findings.json --claims /app/work/claims.json --summary "..." --status partial`
   输入上述 JSON；输出更新后的 result.json（脚本排序、重编号、校验、记 change_log）。coverage/pending_human/limitations/uncertainty 可用 `--coverage/--pending-human/--limitations/--uncertainty` 增量写回。
5. **终检与自检（最后 2–3 次请求）**：执行 `python3 -B scripts/main.py selfcheck --output /app/output`，修复它报出的问题（含摘录与定位不一致）；核对六维 coverage 已标注、`limitations`/`pending_review` 齐全、无编造项，再以 `--status ok|partial|insufficient_evidence` 收尾。

时间纪律：第 1 步必须在前 3 分钟内落盘；第 4 步至少每 5 分钟写回一次；若时间不足，宁可保持 `partial` 并如实写 `limitations`，也不宣称完成全文审查。

## 5. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] 真在审查而非摘要：每条 finding 有 `locator`、逐字 `quote`、`evidence`、`reasoning` 三问、可执行 `suggestion`。（实际效果·任务完成度/可核验性）
- [ ] 六类检查项 `coverage` 全标注，且做了跨章节"承诺—交付"对照。（实际效果·完整性）
- [ ] 未把对照组、明确假设、转述观点误判为缺陷；speaker 已区分。（实际效果·正确性）
- [ ] 严重度有区分且 `reasoning` 三问已填，不是一律"中"。（创新增益·可解释分级）
- [ ] `result.json` 可解析，含 `case_id` + output_contract 全部字段 + `status/sources/uncertainty/change_log`。（可靠体验·稳定性）
- [ ] 空输入/坏 JSON/坏编码/PDF 抽取失败均有降级且记录 `errors/degradations`，不崩溃。（可靠体验·异常处理）
- [ ] 长文截断、章节缺失、无法外部核验的引用均已写入 `limitations`/`pending_review`，未宣称全文已查。（可靠体验）
- [ ] 建议都能落到具体句子（补什么证据、改哪句、如何降级结论强度）。（使用增益·减少人工重写）
- [ ] `selfcheck` 通过；同输入同路径重复运行结果一致（脚本无随机性）。（使用增益·可验证性）
- [ ] `python3 -B scripts/selftest.py` 通过，确定性/契约/降级三项自证。（增益可验证性）

## 6. 降级策略（缺数据/缺软件时）

- 缺材料、无文本层、PDF 抽取失败：脚本在 `degradations` 记录并保持骨架；Agent 可改用平台 pdf-text-tool 抽取后补进 `work/corpus.md`；仍失败则 `status=insufficient_evidence`，`limitations` 写清缺什么、影响哪些检查项，**不编造**。
- 引用无法获取全文：该条标 `pending_review` + `pending_human`，写"需外部核对"，不得断言"引用不支持"。
- 缺 `pdftotext`：`scaffold` 对 PDF 记 `pdftotext_not_found` 于 `degradations`，纯文本材料不受影响；Agent 改用平台 pdf-text-tool 后把文本补进语料。
- 参数缺失：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 写盘失败：脚本用临时文件原子替换；仍失败则输出最小合法 result.json，不让进程崩溃。
- 任何未确认项归入 `uncertainty` 或 `pending_review`；绝不伪造 DOI、数值、引用或软件状态。

## 7. 确定性要求（本版重点）

- 脚本无随机性：update 按 `(source, 定位符, 严重度, 类型, 摘录)` 规范键排序并重编号 `F001/C001/M001…`，同一批产物无论以何顺序写入，result.json 完全一致。
- 易变字段仅 `change_log[].ts` 与 `runtime.seconds_elapsed`（墙钟时间）；其余内容确定性生成。
- `report.md` 由 result.json 确定性渲染；禁止手写报告导致与 JSON 不同步。
- 提交前运行 `python3 -B scripts/selftest.py`，它用合成输入证明"同输入同路径两次产物一致 + 契约回填 + 三类降级不崩溃"；正式案例不预置答案。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 work/corpus.md，产出 findings.json / claims.json / coverage.json 后：
python3 -B scripts/main.py update --output /app/output --work /app/work \
  --findings /app/work/findings.json --claims /app/work/claims.json \
  --coverage /app/work/coverage.json --status partial
python3 -B scripts/main.py selfcheck --output /app/output
python3 -B scripts/selftest.py   # 交付前确定性/契约/降级自证
```

示意输出（标为示例，勿冒充正式案例）：`selfcheck` 打印 `{"ok": true, ...}`；`selftest` 打印全部 `PASS` 且退出码 0；`result.json` 含上述全部字段。正式案例、白板与测评由平台提供，本 Skill 不预置任何答案。
