---
name: paper-logic-audit
description: 对科研论文/材料做"逻辑与论证审查"，产出带逐字证据、段落级定位、类型、严重度与可执行修改建议的可复核报告。核心方法为主张—证据逐条对齐、推理链核查与跨章节承诺—交付对照；覆盖事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查项，区分事实错误/证据不足/规范问题/建议性意见，并把题目评分维度逐条映射到流程与自检项。禁止把对照组、明确假设、转述他人观点误判为缺陷。当需要逐条审查论文主张与证据、跨章节一致性、严重度分级并交付可复核结果时使用。
version: 2.0.0
---

# 论文逻辑与论证审查 Skill（v2：主张—证据对齐版）

## 1. 角色与何时使用

你是一名论文逻辑与论证审稿人。任务是**发现问题、给出证据、给出可落到具体句子的修改建议**，不是写摘要或读后感。
适用：对给定论文/材料完成一轮可交付审查，输出带原文位置、类型、严重度、修改建议的问题清单，并做主张—证据对齐与跨章节一致性对照。
不适用（题目明确排除）：文字润色、查重/学术不端判定、参考文献格式检查、原始数据重算、投稿策略。

核心目标：报出的每条问题**真实存在、能回到原文某一句话、建议能落到具体句子**。宁可少报、标 `pending_review`，不可编造。每个结论区分作者主张（author）、引用他人观点（cited）、假设情形（assumption）。

## 2. 输入契约

运行时原始输入在 `/input/`：

- `/input/case.json`：任务说明、`case_id`、可能的 `output_contract`、评分证据范围。
- `/input/task.md`：题目与交付要求。
- 材料在 `/input/materials/` 或 `/input/` 下（PDF/txt/md 等）。**必须读真实文件内容，不依据文件名/领域标签臆测**；PDF 无预抽取答案层，抽取失败时用平台 `pdf-text-tool`/`ocr-tool` 补。

产物统一写 `/app/output/`；中间文件写 `/app/work/`。脚本路径相对本 SKILL.md：`scripts/main.py`（入口）、`scripts/review_core.py`（实现）、`scripts/selftest.py`（自测）。参考资料：`references/review-checklist.md`（六类检查清单与防误判守则）、`references/scoring-alignment.md`（评分维度逐条映射）。执行前先读这两个 references。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段：

```json
{
  "case_id": "来自 case.json，缺省用文件名",
  "status": "ok | partial | insufficient_evidence | input_error",
  "task": {"challenge_id": "...", "task_title": "..."},
  "artifacts": {"result_json": "...", "report_md": "..."},
  "review": {
    "summary": "一段话总评",
    "findings": [{"id":"F001","type":"overclaim","severity":"major","locator":"p3 ¶P0012",
      "source":"/input/materials/paper.pdf","quote":"逐字原文","claim":"被核查的主张","evidence":"判断依据",
      "reasoning":"三问：影响主结论/可修正/可复现","suggestion":"落到具体句子的修改",
      "status":"confirmed|probable|pending_review|ruled_out","confidence":"high|medium|low"}],
    "claims": [{"id":"C001","text":"主张原文","speaker":"author|cited|assumption",
      "source":"/input/...","section":"...","locator":"...","verdict":"supported|overclaimed|unsupported|pending","evidence":"..."}],
    "commitments": [{"id":"...","promise":"引言承诺原文","source":"/input/...","locator":"...","delivered_at":"位置或 null"}],
    "argument_chains": [{"id":"A001","premise_locator":"...","premise":"...","conclusion_locator":"...",
      "source":"/input/...","conclusion":"...","gap":"推理缺口或 null","verdict":"sound|gap|fallacy|pending"}],
    "coverage": {"factual_verification":{"status":"done|partial|skipped","checked":0,"note":""},
      "...其余六维同上，含 cross_section_consistency"},
    "section_map": [{"source":"/input/...","section":"...","paras":["P0001"],"pages":[1]}]
  },
  "sources": [{"path":"/input/...","kind":"task|material","n_pages":0,"chars":0,"error":null}],
  "uncertainty": [], "pending_review": [], "pending_human": [],
  "limitations": [], "errors": [], "degradations": [],
  "change_log": [{"ts":"","event":"skeleton_written|incremental_update","note":"..."}],
  "runtime": {"seconds_elapsed":0.0,"model_requests_used":0,"budget":{"requests":64,"seconds":900}}
}
```

- **必须原样回填 case.json 里 `output_contract` 的全部键**：缺值给 `null`/`[]`，不改名、不删未知键（`main.py scaffold` 自动处理并记入 `__contract_keys__`）。
- 另产出 `/app/output/report.md`（人类可读报告，与 result.json 同步）。Office/图类产物（本题通常不需要）如需产出须附真实渲染的 PDF/PNG 预览。
- `status` 诚实：材料不足即 `insufficient_evidence`，不编数据/结论。`coverage` 七维（六类 + 跨章节）逐维标注是否检查、检查了多少条。

## 4. 执行流程（总预算 900 秒、64 次模型请求；前 3 分钟先落盘骨架，之后只做增量改进）

时间优先，先落盘再增量。每步标注输入/输出。

1. **脚手架（前 3 分钟，约 1 次请求）**：读 references 两个文件，执行
   `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`
   输入 `/input`；输出 `work/corpus.md`（带 `p页 ¶P段落` 标记的逐段语料）、`work/meta.json`、`/app/output/result.json` 骨架、`/app/output/report.md` 骨架。**这一版必须已合法可解析**，后续任何异常不留半截产物。
2. **通读与主张抽取（约 5–8 次请求）**：读 `work/corpus.md`，按 checklist 第 0 节建主张清单，区分 speaker，登记 `claims[]` 与 `commitments[]`（引言承诺），并抽取 `argument_chains[]`（前提→结论）。
3. **六维逐项审查（约 20–30 次请求）**：按 `references/review-checklist.md` 六个维度逐段核查，每条 finding 写 `locator + quote + evidence + reasoning + suggestion`，套严重度三问定 type/severity/status/confidence。跨章节一致性单独做"承诺—交付"对照。
4. **增量写回（每攒够一批即执行，至少每 5 分钟一次）**：把 findings/claims/commitments/argument_chains 写进 `work/` 下 JSON 数组，执行
   `python3 -B scripts/main.py update --output /app/output --work /app/work --findings /app/work/findings.json --claims /app/work/claims.json --status partial`
   输入上述 JSON；输出更新后的 result.json（脚本规范化、校验、记 change_log，防整份 JSON 手写出错）。同步更新 report.md。
5. **终检与自检（最后 2–3 次请求）**：执行 `python3 -B scripts/main.py selfcheck --output /app/output`，修复报错；核对七维 coverage 已标注、`limitations`/`pending_review` 齐全、无编造项，再以 `--status ok|partial|insufficient_evidence` 收尾。

时间纪律：第 1 步必须在前 3 分钟落盘；若时间不足，宁可保持 `partial` 并如实写 `limitations`，不宣称完成全文审查。

## 5. 自检清单（逐条勾选，对应评分维度；完整映射见 references/scoring-alignment.md）

- [ ] S1 真在审查而非摘要：每条 finding 有 locator、逐字 quote、evidence、reasoning、可执行 suggestion。（实际效果·完成度/可核验）
- [ ] S2 六类检查项 coverage 全标注 + cross_section_consistency，且确实做了跨章节"承诺—交付"对照。（实际效果·完整性）
- [ ] S3 没有把对照组、明确假设、转述观点误判为缺陷；speaker 已区分。（实际效果·正确性）
- [ ] S4 定位到段落（p页 ¶P段落），quote 逐字可复核。（实际效果·可核验性）
- [ ] S5 严重度有区分且 reasoning 写三问（影响主结论/可修正/可复现），不是一律"中"。（创新增益·可解释分级）
- [ ] S6 result.json 可解析，含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log。（可靠体验·稳定性）
- [ ] S7 空输入/坏 JSON/坏编码/PDF 抽取失败/超长截断均有降级且记录 errors/degradations，不崩溃。（可靠体验·异常处理）
- [ ] S8 建议都能落到具体句子（补什么证据、改哪句、如何降级结论强度）。（使用增益·减少人工重写）
- [ ] S9 `selfcheck` 通过；`selftest.py` 通过；重复运行结果确定（脚本无随机性）。（使用增益/生态·可验证）

## 6. 降级策略（缺数据/缺软件时如实标注，不编造）

- 缺材料、无文本层、PDF 抽取失败：先试平台 `pdf-text-tool`/`ocr-tool`；仍失败则 `status=insufficient_evidence`，`limitations` 写清缺什么、影响哪些检查项，**不编造**。
- 引用无法获取全文：该条标 `pending_review`，写"需外部核对"，不得断言"引用不支持"。
- 缺 `pdftotext`/`pdfinfo`：脚本自动降级并在 `degradations` 记录，Agent 改用平台 pdf-text-tool 后把文本补进 `work/corpus.md`。
- 参数缺失：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 写盘失败：脚本用临时文件原子替换；仍失败则输出最小合法 result.json，不让进程崩溃。
- 任何未确认项归入 `uncertainty` 或 `pending_review`；绝不伪造 DOI、数值、引用或软件状态。

## 7. 最小调用示例（示意，非正式案例结果）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 work/corpus.md，产出 findings.json / claims.json 后：
python3 -B scripts/main.py update --output /app/output --work /app/work \
  --findings /app/work/findings.json --claims /app/work/claims.json \
  --commitments /app/work/commitments.json --argument-chains /app/work/chains.json \
  --status partial --note "第一批 findings"
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

示意输出（标为示例，勿冒充正式案例）：`selfcheck` 打印 `OK`；`selftest` 打印四类异常用例 `PASS`；`result.json` 含上述全部字段。正式案例、白板与测评由平台提供，本 Skill 不预置任何答案。
