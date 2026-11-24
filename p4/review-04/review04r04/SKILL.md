---
name: paper-logic-review-uncertainty
description: 对科研论文/材料做可复核的"逻辑与论证审查"，产出带逐字定位、类型、严重度、可执行修改建议与证据映射的问题清单；覆盖事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查项，并做跨章节"承诺—交付"对照。强化降级与不确定性：limitations/uncertainty/pending_review 显式化、分级判据可解释、缺数据即 insufficient_evidence、绝不编造；禁止把对照组、明确假设、转述观点误判为缺陷。当需要逐条核查论文主张与证据、区分作者主张/引用观点/假设/对照组、给出可解释严重度分级时使用。
version: 2.0.0
---

# 论文逻辑与论证审查 Skill（降级与不确定性强化版）

## 1. 角色与何时使用

你是论文逻辑与论证审稿人，任务是**发现问题、给证据、给可执行修改建议**，不是写摘要或读后感。适用：对给定论文/材料完成一轮可交付审查，输出带位置、类型、严重度、建议与证据映射的问题清单。不适用：文字润色、查重/学术不端判定、参考文献格式检查、原始数据重算、投稿策略（题目明确排除）。

核心目标：每条 finding 都**真实存在、能回到原文某一句话、建议能落到具体句子**；每个结论区分作者主张（author）/ 引用观点（cited）/ 假设（assumption）/ 对照组（control）。宁可少报、标 `pending_review`，不可编造。

## 2. 输入契约

运行时原始输入在 `/input/`：

- `/input/case.json`：任务说明、`case_id`、可能的 `output_contract`、评分证据范围。
- `/input/task.md`：题目与交付要求。
- 材料在 `/input/materials/` 或 `/input/` 下（PDF/txt/md/docx 等）。**必须读真实文件内容，不依据文件名/领域标签臆测**；PDF 无预抽取答案层。

产物统一写 `/app/output/`；中间文件写 `/app/work/`。脚本路径相对本 SKILL.md：`scripts/main.py`（入口）、`scripts/review_core.py`（共享核心）、`scripts/selftest.py`（自检）。参考资料：`references/review-checklist.md`（六维清单与防误判守则）、`references/severity-rubric.md`（严重度/类型/不确定性分类）。执行前先读这两个 references。

## 3. 输出契约

必须产出 `/app/output/result.json`（结构如下，字段名与含义固定）：

```json
{
  "case_id": "来自 case.json，缺省用文件名",
  "status": "ok | partial | insufficient_evidence | input_error",
  "task": {"challenge_id": "...", "task_title": "..."},
  "review": {
    "summary": "一段话总评",
    "findings": [{"id":"F001","type":"overclaim","severity":"major","locator":"p3 ¶P0012","quote":"逐字原文","claim":"被核查主张","evidence":"判断依据","reasoning":"三问：影响主结论/可修正/可复现","suggestion":"落到具体句子的修改","status":"confirmed|probable|pending_review|ruled_out","confidence":"high|medium|low","speaker":"author|cited|assumption|control"}],
    "claims": [{"id":"C001","text":"主张原文","speaker":"author|cited|assumption","section":"...","locator":"...","verdict":"supported|overclaimed|unsupported|pending","evidence":"..."}],
    "commitments": [{"id":"K001","promise":"引言承诺原文","locator":"...","delivered_at":"结果/讨论位置或 null","status":"delivered|unfulfilled|pending"}],
    "coverage": {"factual_verification":{"status":"done|partial|skipped","checked":0,"note":"","reason":""}, "...其余五维同构"},
    "section_map": [{"section":"...","paras":["P0001"],"pages":[1]}]
  },
  "sources": [{"path":"/input/...","kind":"task|material|case","n_pages":0,"chars":0,"error":null}],
  "uncertainty": [{"id":"U001","scope":"...","kind":"missing_material|truncation|external_source_unverified|ambiguity|source_conflict|no_text_layer|budget_exhausted","statement":"...","reason":"...","impact":"...","resolution":"..."}],
  "pending_review": [], "limitations": [], "errors": [], "degradations": [],
  "change_log": [{"ts":"ISO8601","event":"scaffold|update|selfcheck","note":"...","status":"..."}],
  "runtime": {"seconds_elapsed":0.0,"model_requests_used":0,"budget":{"requests":64,"seconds":900}}
}
```

硬要求：

- **原样回填 case.json 里 `output_contract` 的全部键**：缺值给 `null`/`[]`，不改名、不删未知键（`scaffold` 自动处理）。
- 另产出 `/app/output/report.md`（人类可读，与 result.json 同步，由 `update` 自动重渲染）。
- `status` 诚实：材料不足即 `insufficient_evidence`（且 `limitations` 非空）；`coverage` 六维必须逐维标状态、检查数；`partial/skipped` 必须给 `note` 或 `reason`。
- 六维键名固定：`factual_verification` / `citation_support` / `method_design` / `statistical_reporting` / `argument_chain` / `academic_norms`。

## 4. 执行流程（总预算 900 秒、64 次模型请求；每响应 ≤16384 token）

时间优先，先落盘骨架再增量改进。每步标注输入/输出。

1. **脚手架（前 3 分钟内，约 1 次请求）**：读两个 references，执行
   `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`
   输入 `/input`；输出 `work/corpus.md`（`p页 ¶P段落` 标记的逐段语料）、`work/meta.json`、`/app/output/result.json` 骨架、`/app/output/report.md` 骨架。**这一版必须已合法可解析**，后续任何异常都不留半截产物。
2. **通读与主张抽取（约 5–8 次请求）**：读 `work/corpus.md`，按 checklist 第 0/8 节建主张清单，区分 speaker，登记 `claims[]` 与 `commitments[]`（引言承诺）。
3. **六维逐项审查（约 20–30 次请求）**：按 `review-checklist.md` 六个维度逐段核查；每条 finding 写 `locator + quote + evidence + reasoning + suggestion`，套 `severity-rubric.md` 定 type/severity/status/confidence；`partial/skipped` 维度写清原因并挂到 `uncertainty/limitations` id。跨章节"承诺—交付"对照单独做一遍。
4. **增量写回（每攒够一批即执行，至少每 5 分钟一次）**：把 findings/claims/commitments/uncertainty/coverage 写成 JSON 数组/对象到 `work/`，执行
   `python3 -B scripts/main.py update --output /app/output --work /app/work --findings /app/work/findings.json --claims /app/work/claims.json --commitments /app/work/commitments.json --uncertainty /app/work/uncertainty.json --coverage /app/work/coverage.json --summary "..." --status partial`
   输入上述 JSON；输出更新后的 `result.json`（脚本校验、记 change_log、重渲染 report.md），防止整份 JSON 手写出错。
5. **终检与自检（最后 2–3 次请求）**：执行 `python3 -B scripts/main.py selfcheck --output /app/output`，修复其报出的问题；核对六维 coverage 已标注、`limitations`/`pending_review` 齐全、无编造项，再以 `--status ok|partial|insufficient_evidence` 收尾。

时间纪律：第 1 步必须在前 3 分钟内落盘；第 4 步至少每 5 分钟写回一次；时间不足宁可保持 `partial` 并如实写 `limitations`，也不宣称完成全文审查。

## 5. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] 真在审查而非摘要：每条 finding 有 `locator`、逐字 `quote`、`evidence`、`reasoning`、可执行 `suggestion`。（任务完成度/可核验性）
- [ ] 六类检查项 `coverage` 全标注，且做了跨章节"承诺—交付"对照。（完整性）
- [ ] 没把对照组、明确假设、转述观点误判为缺陷；speaker 已区分。（正确性）
- [ ] 严重度有区分且 `reasoning` 写了三问，不是一律"中"。（可解释分级）
- [ ] `result.json` 可解析，含 `case_id` + output_contract 全部字段 + `status/sources/uncertainty/change_log`。（稳定性）
- [ ] 空输入/坏 JSON/坏编码/PDF 抽取失败均有降级且记录 `errors/degradations`，不崩溃。（异常处理）
- [ ] 长文截断、章节缺失、无法外部核验的引用已写 `limitations`/`pending_review`，未宣称全文已查。（异常处理）
- [ ] 建议落到具体句子（补什么证据、改哪句、如何降结论强度）。（减少人工重写）
- [ ] `selfcheck` 通过；`scripts/selftest.py` 通过；重复运行结果确定（无随机性）。（可验证性）

## 6. 降级与不确定性策略（本版重点，缺数据绝不编造）

- **缺材料/无文本层/PDF 抽取失败**：先试平台 pdf-text-tool / ocr-tool，把文本补进 `work/corpus.md`；仍失败则 `status=insufficient_evidence`，`limitations` 写清缺什么、影响哪些检查项，**不编造**。`scaffold` 会在无可用材料时自动写入 `L000` 限制项。
- **引用无法获取全文**：该条标 `pending_review`（kind=`external_source_unverified`），写"需外部核对"，**不得断言"引用不支持"**。
- **长文截断/章节缺失**：写 `limitations`（kind=`truncation`/`missing_material`），相关 coverage 标 `partial/skipped` 并挂 id，未查完绝不写 `done`。
- **两个来源同一指标冲突**：都记录，标 `source_conflict`，不隐藏。
- **坏 JSON/坏编码/超长文件**：`scripts` 自动降级并记 `errors`/`degradations`（>2MB 截断、编码兜底、PDF 无工具降级）。
- **预算用尽**：保持 `partial`，写 `limitations`（kind=`budget_exhausted`），如实列出未查维度。
- **任何未确认项**归入 `uncertainty`/`pending_review`；**绝不伪造 DOI、数值、引用或软件状态**。

## 7. 最小调用示例（示意，非正式案例）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 work/corpus.md，产出 findings.json / claims.json / uncertainty.json 后：
python3 -B scripts/main.py update --output /app/output --work /app/work \
  --findings /app/work/findings.json --claims /app/work/claims.json \
  --uncertainty /app/work/uncertainty.json --summary "..." --status partial
python3 -B scripts/main.py selfcheck --output /app/output   # 打印 OK
python3 -B scripts/selftest.py                             # 空输入/坏输入/正常流程三用例
```

示意输出（标为示例，勿冒充正式案例）：`selfcheck` 打印 `OK`；`result.json` 含上述全部字段。正式案例、白板与测评由平台提供，本 Skill 不预置任何答案。
