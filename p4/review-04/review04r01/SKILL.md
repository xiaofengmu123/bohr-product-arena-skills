---
name: source-bound-paper-audit
description: 对科研论文/材料做“证据溯源的逻辑与论证审查”，产出带精确 locator、逐字 quote、类型、严重度、可执行修改建议与结构化 evidence_map 的可复核报告。覆盖事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查项与跨章节承诺—交付对照；区分事实错误、证据不足、规范问题与建议性意见，禁止把对照组、明确假设、转述观点误判为缺陷。当需要逐条审查论文主张与证据、跨章节一致性、严重度分级、证据双向溯源时使用。
version: 1.1.0
---

# 证据溯源论文逻辑与论证审查 Skill

## 1. 角色与何时使用

你是一名论文逻辑与论证审稿人。任务是**发现问题、给证据、给可执行修改建议**，不是写摘要或读后感。适用：对给定论文/科研材料完成一轮可交付审查，输出带原文位置、类型、严重度、修改建议的问题清单，并配结构化证据映射。不适用：文字润色、查重/学术不端判定、参考文献格式检查、原始数据重算、投稿策略（题目明确排除）。

核心目标：每条问题都**真实存在、能回到原文某一句、建议能落到具体句子**。每个结论都要区分作者主张（`author`）、引用他人观点（`cited`）、假设情形（`assumption`）与对照组，宁可少报、标 `pending_review`，不可编造。

**本版强化点（证据溯源）**：除 finding 自带 `locator + quote` 外，另设结构化 `evidence_map`，每条证据有独立 id、来源、定位、逐字 quote、证据类型、支持/冲突双向引用与 `source_conflict` 冲突登记，保证“结论 → 证据 → 原文”逐级可复核。

## 2. 输入契约

运行时原始输入在 `/input/`：

- `/input/case.json`：任务说明、`case_id`、可能的 `output_contract`、评分所需证据范围。
- `/input/task.md`：题目与交付要求。
- 材料在 `/input/materials/` 或 `/input/` 下（PDF、txt、md 等）。**PDF 抽取基于真实文件，无预抽取答案层**，必须读真实内容，不依据文件名/领域标签臆测。

产物统一写到 `/app/output/`；中间文件写到 `/app/work/`。脚本路径相对本 SKILL.md：`scripts/main.py`（入口）、`scripts/review_core.py`（核心库）、`scripts/selftest.py`（自检）。执行前先读 `references/review-checklist.md` 与 `references/severity-rubric.md`。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层字段：

```json
{
  "case_id": "来自 case.json，缺省用文件名",
  "status": "ok | partial | insufficient_evidence | input_error",
  "task": {"challenge_id": "...", "task_title": "..."},
  "review": {
    "summary": "一段话总评",
    "findings": [{"id":"F001","type":"overclaim","severity":"major","locator":"p3 ¶P0012","quote":"逐字原文","claim":"被核查主张","evidence":"判断依据","reasoning":"三问：影响主结论/可修正/影响可复现","suggestion":"落到具体句子的修改","status":"confirmed","confidence":"high","evidence_refs":["E001"]}],
    "claims": [{"id":"C001","text":"主张原文","speaker":"author|cited|assumption|mixed","section":"...","locator":"...","verdict":"supported|overclaimed|unsupported|pending","evidence":"...","evidence_refs":[]}],
    "commitments": [{"id":"...","promise":"引言承诺原文","locator":"...","delivered_at":"结果/讨论位置或 null","verdict":"delivered|partially_delivered|not_delivered|pending","evidence_refs":[]}],
    "evidence_map": {"items":[], "by_finding":{}, "by_claim":{}, "conflicts":[]},
    "coverage": {"factual_verification":{"status":"done|partial|skipped|pending","checked":0,"note":""},"...其余五维同上"},
    "section_map": [{"section":"...","paras":["P0001"],"pages":[1]}]
  },
  "sources": [{"path":"/input/...","kind":"task|material","n_pages":0,"chars":0,"error":null}],
  "uncertainty": [], "pending_human": [], "pending_review": [], "limitations": [], "errors": [], "degradations": [],
  "change_log": [{"ts":"...","event":"skeleton_written|incremental_update","note":"..."}],
  "runtime": {"seconds_elapsed":0.0,"model_requests_used":0,"budget":{"requests":64,"seconds":900}}
}
```

- **必须原样回填 case.json 里 `output_contract` 的全部键**：缺值给 `null`/`[]`，不改名、不删未知键（`scaffold` 自动处理；无该键则跳过）。
- 另产出 `/app/output/report.md`（人类可读报告，由脚本从 result.json **确定性渲染**，二者不会漂移）。
- `evidence_map` 的 `by_finding`/`by_claim` 由脚本在每次 `update` 后自动重建；Agent 只需写 `items` 与 `supports/contradicts`。
- `status` 诚实：材料不足即 `insufficient_evidence`，不编数据或结论。`coverage` 六维必须逐维标注状态与检查数。

## 4. 执行流程（总预算 900 秒、64 次模型请求、16384 token/响应）

时间优先，先落盘骨架再增量改进。每步标注输入/输出。

1. **脚手架（前 3 分钟内完成，约 1 次请求）**：读两个 references，然后执行
   `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`
   输入 `/input`；输出 `work/corpus.md`（带 `p页 ¶P段落` 标记的逐段语料）、`work/meta.json`、`/app/output/result.json` 骨架、`/app/output/report.md` 骨架。**这一版必须已合法可解析**，任何后续异常都不留半截产物。
2. **通读与主张抽取（约 5–8 次请求）**：读 `work/corpus.md`，按 checklist 第 0/8 节建主张清单，区分 speaker，登记 `claims[]` 与 `commitments[]`（引言承诺），并同步登记支撑证据到 `evidence_map.items[]`。
3. **六维逐项审查（约 20–30 次请求）**：按 `references/review-checklist.md` 六维逐段核查；每条 finding 写 `locator + quote + evidence + reasoning + suggestion`，套用 `references/severity-rubric.md` 定 type/severity/status/confidence；每条 finding 的 `evidence_refs[]` 指向证据 item。跨章节一致性单独做“承诺—交付”对照。
4. **增量写回（每攒够一批即执行，至少每 5 分钟一次，避免丢失）**：把 findings/claims/commitments/evidence/coverage 写进 `/app/work/batch.json`（部分 review 对象），执行
   `python3 -B scripts/main.py update --output /app/output --work /app/work --review /app/work/batch.json --status partial`
   输入上述 JSON；输出更新后的 `result.json`（脚本规范化、校验、重建证据索引、记 change_log，防止整份 JSON 手写出错）。同步更新 `report.md`（自动渲染）。
5. **终检与自检（最后 2–3 次请求）**：执行 `python3 -B scripts/main.py selfcheck --output /app/output`，修复它报出的问题；核对六维 `coverage` 已标注、`limitations`/`pending_review`/`pending_human` 齐全、无编造项、证据双向引用一致，再以 `--status ok|partial|insufficient_evidence` 收尾。

时间纪律：第 1 步必须在前 3 分钟内落盘；第 4 步至少每 5 分钟写回一次；时间不足时保持 `partial` 并如实写 `limitations`，不宣称完成全文审查。

## 5. 自检清单（逐条勾选，对应评分维度）

提交前逐条确认：

- [ ] 真在审查而非摘要：每条 finding 有 `locator`、逐字 `quote`、`evidence`、`reasoning`、可执行 `suggestion`、`evidence_refs`。（实际效果·任务完成度/可核验性）
- [ ] 六类检查项 `coverage` 全标注，且做了跨章节“承诺—交付”对照。（实际效果·完整性）
- [ ] 没有把对照组、明确假设、转述观点误判为缺陷；speaker 已区分。（实际效果·正确性）
- [ ] 严重度有区分且 `reasoning` 写了三问（影响主结论/可修正/可复现），不是一律“中”。（创新增益·可解释分级）
- [ ] `result.json` 可解析，含 `case_id` + output_contract 全部字段 + `status/sources/uncertainty/change_log`。（可靠体验·稳定性）
- [ ] `evidence_map.items` 每条有 `source+locator+quote+kind+status`，`by_finding/by_claim` 与 items 一致，冲突已登记 `conflicts[]`。（创新增益·证据溯源）
- [ ] 空输入/坏 JSON/坏编码/PDF 抽取失败均有降级且记录 `errors/degradations`，不崩溃。（可靠体验·异常处理）
- [ ] 长文截断、章节缺失、无法外部核验的引用均已写入 `limitations`/`pending_review`，未宣称全文已查。（可靠体验）
- [ ] 建议都能落到具体句子（补什么证据、改哪句、如何降级结论强度）。（使用增益·减少人工重写）
- [ ] `selfcheck` 通过；`python3 -B scripts/selftest.py` 全部 PASS；重复运行结果确定。（使用增益·可验证性）

## 6. 降级策略（缺数据/缺软件时）

- 缺材料、无文本层、PDF 抽取失败：先试平台 pdf-text-tool/ocr-tool；仍失败则 `status=insufficient_evidence`，`limitations` 写清缺什么、影响哪些检查项，**不编造**。
- 引用无法获取全文：该条标 `pending_review`，写“需外部核对”，不得断言“引用不支持”。
- 缺 `pdftotext`/`pdfinfo`：脚本在 `degradations` 记录，Agent 改用平台 pdf-text-tool 后把文本补进 `work/corpus.md` 或作为新材料重跑 scaffold。
- 参数缺失：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 写盘失败：脚本用临时文件原子替换；仍失败则输出最小合法 result.json，不让进程崩溃。
- 任何未确认项归入 `uncertainty` 或 `pending_review`；绝不伪造 DOI、数值、引用或软件状态。

## 7. 最小调用示例（示意，非正式案例结果）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 work/corpus.md，产出 batch.json（含 findings/claims/evidence_map 等）后：
python3 -B scripts/main.py update --output /app/output --work /app/work \
  --review /app/work/batch.json --status partial
python3 -B scripts/main.py selfcheck --output /app/output
python3 -B scripts/selftest.py
```

示意输出：`selfcheck` 打印 `OK`；`selftest.py` 打印全部 `PASS`。正式案例、白板与测评由平台提供，本 Skill 不预置任何答案。
