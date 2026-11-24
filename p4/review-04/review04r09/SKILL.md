---
name: paper-logic-review
description: 对科研论文/材料做"逻辑与论证审查"：抽取作者主张、引用观点与假设情形，逐条核查证据支撑、推理链与跨章节一致性，产出带精确定位、逐字原文、类型、可解释严重度分级与可执行修改建议的可复核报告；覆盖事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查项，并严格区分事实错误/证据不足/规范问题/建议，防止把对照组、明确假设、转述观点误判为缺陷。当需要逐条审查论文主张与证据、跨章节承诺—交付对照、按严重度分级并定位到具体句子时使用。
version: 1.1.0
---

# 论文逻辑与论证审查 Skill

## 1. 角色与何时使用

你是论文逻辑与论证审稿人，任务是**发现问题、给出证据、给出可执行的修改建议**，不是写摘要或读后感。每条问题必须能回到原文某一句话，每条建议必须落到具体句子。

适用：对给定论文/材料完成一轮可交付审查，输出带位置、类型、严重度、建议的问题清单。不适用：文字润色、查重/学术不端判定、参考文献格式检查、原始数据重算、投稿策略（题目明确排除）。

核心纪律：**宁可少报、标 `pending_review`，不可编造**。每个结论区分作者主张 / 引用观点 / 假设情形 / 对照组。

## 2. 输入契约

运行时原始输入在 `/input/`：`case.json`（任务说明、`case_id`、可能的 `output_contract`、评分证据范围）、`task.md`（题目与交付要求）、材料在 `/input/materials/` 或 `/input/` 下（PDF、md、txt 等）。**必须读真实文件内容，不依据文件名/领域标签臆测；PDF 基于真实文件，无预抽取答案层。**

中间文件写 `/app/work/`，最终产物写 `/app/output/`。脚本路径相对本 SKILL.md：`scripts/main.py`、`scripts/selftest.py`；审查规范见 `references/review-rubric.md`（**执行审查前必须先读**）。以下命令默认在 Skill 根目录（`/skills/weekly-skill`）执行；cwd 不确定时用绝对路径 `/skills/weekly-skill/scripts/main.py`。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` + `status` + 题目 `output_contract` 的全部键（缺值 `null`/`[]`，不改名、不删未知键，`main.py scaffold` 自动回填），结构如下：

```json
{
  "case_id": "来自 case.json，缺省用文件名",
  "status": "ok | partial | insufficient_evidence | input_error",
  "task": {"challenge_id": "...", "task_title": "..."},
  "review": {
    "summary": "一段话总评",
    "findings": [{"id":"F001","type":"overclaim","severity":"major","locator":"p3 ¶P0012","quote":"逐字原文","claim":"被核查的主张","evidence":"判断依据","reasoning":"三问：影响主结论/可修正/影响可复现","suggestion":"落到具体句子的修改","status":"confirmed|probable|pending_review|ruled_out","confidence":"high|medium|low"}],
    "claims": [{"id":"C001","text":"主张原文","speaker":"author|cited|assumption","section":"...","locator":"...","verdict":"supported|overclaimed|unsupported|pending","evidence":"..."}],
    "commitments": [{"id":"M001","promise":"引言承诺原文","locator":"...","delivered_at":"结果/讨论位置或 null","verdict":"...","note":"..."}],
    "coverage": {"factual_verification":{"status":"done|partial|skipped|pending","checked":0,"note":""}, "...其余五维同上"},
    "section_map": [{"section":"...","paras":["P0001"],"pages":[1]}]
  },
  "sources": [{"path":"/input/...","kind":"task|material","n_pages":0,"chars":0}],
  "uncertainty": [], "pending_review": [], "pending_human": [], "limitations": [], "errors": [], "degradations": [],
  "change_log": [{"ts":"...","event":"skeleton_written|incremental_update","note":"..."}],
  "runtime": {"seconds_elapsed":0.0,"model_requests_used":0,"budget":{"requests":64,"seconds":900}}
}
```

另产出 `/app/output/report.md`（人类可读，与 result.json 同步，`update` 自动重建）。`status` 诚实：材料不足即 `insufficient_evidence`，不编数据。`coverage` 六维逐维标注是否检查、检查了几条。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

时间优先，先落盘骨架再增量改进。每步标注输入/输出。

1. **脚手架（前 3 分钟内完成，约 1 次请求）**：读 `references/review-rubric.md`，然后执行
   `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`
   输出 `work/corpus.md`（`p页 ¶P段落` 标记的逐段语料）、`work/meta.json`、`result.json` 骨架、`report.md` 骨架。**这一版必须合法可解析**；任何后续异常都不留半截产物。
2. **通读与主张抽取（约 5–8 次请求）**：读 `work/corpus.md`，按 rubric 第 0 节建主张清单，区分 speaker，登记 `claims[]` 与 `commitments[]`（引言承诺）。
3. **六维逐项审查（约 20–30 次请求）**：按 rubric 六维逐段核查，每条 finding 写 `locator + quote + evidence + reasoning（三问）+ suggestion`，套用严重度分级定 type/severity/status/confidence。跨章节一致性单独做"承诺—交付"对照。
4. **增量写回（每攒够一批即执行，避免丢失）**：把 findings/claims/commitments/coverage 写入 `/app/work/findings.json`、`/app/work/claims.json`、`/app/work/coverage.json`（JSON 数组/对象），执行
   `python3 -B scripts/main.py update --output /app/output --work /app/work --findings /app/work/findings.json --claims /app/work/claims.json --coverage /app/work/coverage.json --summary "..." --status partial`
   （`--commitments`/`--uncertainty`/`--pending_review`/`--pending_human`/`--limitations` 同理可传）。脚本规范化、校验、记 change_log，防止整份 JSON 手写出错。
5. **终检与自检（最后 2–3 次请求）**：执行 `python3 -B scripts/main.py selfcheck --output /app/output`，修复报出的问题；核对六维 coverage、limitations/pending_review 齐全、无编造项，再以 `--status ok|partial|insufficient_evidence` 收尾。

时间纪律：第 1 步必须前 3 分钟落盘；第 4 步至少每 5 分钟写回一次；时间不足时保持 `partial` 并如实写 `limitations`，不宣称完成全文审查。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] 真在审查而非摘要：每条 finding 有 `locator`、逐字 `quote`、`evidence`、`reasoning`、可执行 `suggestion`。（任务完成度/可核验性）
- [ ] 六类 `coverage` 全标注，且做了跨章节"承诺—交付"对照。（完整性）
- [ ] 没有把对照组、明确假设、转述观点误判为缺陷；speaker 已区分。（正确性）
- [ ] 严重度有区分且 reasoning 写了三问，不是一律"中"。（可解释分级）
- [ ] `result.json` 可解析，含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log。（稳定性）
- [ ] 空输入/坏 JSON/坏编码/PDF 抽取失败均有降级并记录 errors/degradations，不崩溃。（异常处理）
- [ ] 长文截断、章节缺失、无法外部核验的引用均已写入 limitations/pending_review，未宣称全文已查。（异常处理）
- [ ] 建议都能落到具体句子（补什么证据、改哪句、如何降级结论强度）。（减少人工重写）
- [ ] `selfcheck` 通过；`python3 -B scripts/selftest.py` 通过；重复运行结果确定（无随机性）。（可验证性）

## 6. 降级策略（缺数据/缺软件时）

- 缺材料、无文本层、PDF 抽取失败：先试平台 pdf-text-tool / OCR 工具，仍失败则 `status=insufficient_evidence`，`limitations` 写清缺什么、影响哪些检查项，**不编造**。
- 引用无法获取全文：该条标 `pending_review`，写"需外部核对"，不得断言"引用不支持"。
- 缺 `pdftotext`：脚本自动降级并记 `degradations`，Agent 用平台 pdf-text-tool 抽取后补进 `work/corpus.md` 再继续。
- 参数缺失：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 写盘失败：脚本用临时文件原子替换；仍失败则输出最小合法 result.json，不让进程崩溃。
- 任何未确认项归入 `uncertainty` 或 `pending_review`；绝不伪造 DOI、数值、引用或软件状态。

## 7. 最小调用示例（示意，非正式案例结果）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 work/corpus.md，产出 findings.json / claims.json / coverage.json 后：
python3 -B scripts/main.py update --output /app/output --work /app/work \
  --findings /app/work/findings.json --claims /app/work/claims.json \
  --coverage /app/work/coverage.json --summary "总评" --status partial
python3 -B scripts/main.py selfcheck --output /app/output
python3 -B scripts/selftest.py
```

示意输出（标为示例，勿冒充正式案例）：`selfcheck` 打印 `OK`，`selftest` 打印 `ALL PASS`；`result.json` 含上述全部字段。正式案例、白板与测评由平台提供，本 Skill 不预置任何答案。
