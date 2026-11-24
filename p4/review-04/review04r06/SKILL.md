---
name: paper-logic-audit
description: 对科研论文/材料做"逻辑与论证审查"，产出带段落级定位、类型、严重度、可执行修改建议与机器可复核证据索引的报告。覆盖事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查；区分事实错误、证据不足、规范问题与建议性意见；通过结构化中间表示（review_model/plan/evidence_map）保证主张—证据对齐与跨章节承诺—交付一致性可复核；禁止把对照组、明确假设、转述观点误判为缺陷。当需要逐条审查论文主张与证据、跨章节一致性、严重度分级并留下可复核记录时使用。
version: 1.1.0
---

# 论文逻辑与论证审查 Skill

## 1. 角色与何时使用

你是论文逻辑与论证审稿人。任务是**发现问题、给出证据、给出可落到句子的修改建议**，
不是写摘要或读后感。适用：对给定论文/科研材料完成一轮可交付审查，输出带原文位置、
类型、严重度和修改建议的问题清单，并留下可复核的中间表示。不适用：文字润色、查重/
学术不端判定、参考文献格式检查、原始数据重算、投稿策略（题目明确排除）。

核心目标：每条问题都**真实存在、能回到原文某一句话、建议能落到具体句子**。宁可少报、
标 `pending_review`，不可编造。每个结论都区分 `author / cited / assumption`。

## 2. 输入契约

运行时原始输入在 `/input/`：

- `/input/case.json`：任务说明、`case_id`、可能的 `output_contract`、评分证据范围。
- `/input/task.md`：题目与交付要求。
- 材料在 `/input/materials/` 或 `/input/` 下（PDF、txt、md 等）。**必须读真实文件内容，
  不依据文件名/领域标签臆测**。PDF 抽取基于真实文件，无预抽取答案层。

产物统一写到 `/app/output/`；中间文件写到 `/app/work/`。脚本相对本 SKILL.md：
`scripts/main.py`、`scripts/selftest.py`。执行前先读 `references/review-checklist.md`
与 `references/severity-rubric.md`。

## 3. 中间表示（本版核心：可复核的设计取舍）

与"只留最终报告"不同，本 Skill 把审查过程沉淀为**可机读、可重建**的中间表示，评审
可从任一条 finding 反查其证据段落，且中间表示由脚本校验、不得与最终产物漂移：

| 文件 | 性质 | 内容 |
|---|---|---|
| `work/corpus.md` + `work/corpus.json` | 只读底料 | 带 `p<页> ¶P<段落>` 定位符的逐段语料（人读 md / 机读 json） |
| `work/meta.json` | 只读底料 | 输入发现结果：来源、大小、抽取错误 |
| `work/review_model.json` | **唯一手改中间表示** | versioned 模型：summary / claims / commitments / findings / coverage / 各类边界 |
| `work/plan.json` | 脚本派生 | 六维进度状态机，由 coverage 确定性重建，防止"宣称已查而模型为空" |
| `work/evidence_map.json` | 脚本派生 | finding ID → 段落指针与逐字 quote，由 review_model + corpus 重建 |

`review_model.json` 是 Agent 唯一需要手工编辑的文件；`result.json`、`plan.json`、
`evidence_map.json`、`report.md` 全部由脚本从它派生，保证四者同步。任何手写错误由
`update` 逐条报告（剔除非法条目并记入 `errors`），不会静默丢字段。

## 4. 输出契约

必须产出 `/app/output/result.json`（结构见下）与 `/app/output/report.md`（人读报告，
与 result.json 同步）。`result.json` 字段：

```json
{
  "case_id": "来自 case.json，缺省用文件名",
  "status": "ok | partial | insufficient_evidence | input_error",
  "task": {"challenge_id": "...", "task_title": "..."},
  "review": {
    "summary": "一段话总评",
    "findings": [{"id":"F001","type":"overclaim","severity":"major","locator":"p3 ¶P0012",
      "quote":"逐字原文","claim":"被核查的主张","evidence":"判断依据",
      "reasoning":"三问：影响主结论/可修正/影响可复现","suggestion":"落到具体句子的修改",
      "status":"confirmed|probable|pending_review|ruled_out","confidence":"high|medium|low",
      "evidence_refs":["P0012"]}],
    "claims": [{"id":"C001","text":"主张原文","speaker":"author|cited|assumption",
      "section":"...","locator":"...","verdict":"supported|overclaimed|unsupported|pending",
      "evidence":"..."}],
    "commitments": [{"id":"M001","promise":"引言承诺原文","locator":"...",
      "delivered_at":"结果/讨论位置或 null","status":"delivered|missing|partial|pending"}],
    "coverage": {"factual_verification":{"status":"done|partial|skipped","checked":0,"note":""},
      "...其余五维同上"},
    "section_map": [{"section":"...","paras":["P0001"],"pages":[1]}]
  },
  "evidence_map": [{"finding_id":"F001","refs":[{"paragraph_id":"P0012","locator":"...","quote":"..."}]}],
  "sources": [{"path":"/input/...","kind":"task|material|contract","n_pages":0,"chars":0}],
  "uncertainty": [], "pending_review": [], "pending_human": [], "limitations": [],
  "errors": [], "degradations": [],
  "change_log": [{"ts":"...","event":"scaffold_written|incremental_update","note":"..."}],
  "runtime": {"seconds_elapsed":0.0,"model_requests_used":0,"budget":{"requests":64,"seconds":900}}
}
```

- **必须原样回填 case.json 里 `output_contract` 的全部键**：顶层合并 + 保留
  `output_contract` 副本；缺值给 `null`/`[]`，不改名、不删未知键（`scaffold` 自动处理）。
- `status` 诚实：材料不足即 `insufficient_evidence`，不编数据或结论。
- `coverage` 六维必须逐维标注状态与检查数；`evidence_map` 由脚本派生，每个 finding
  必须有可复核的段落指针与逐字 quote。

## 5. 执行流程（总预算 900 秒、64 次模型请求）

时间优先：**前 3 分钟先落盘一版 partial 骨架，之后只做增量改进**。每步标注输入/输出。

1. **脚手架（≤3 分钟，约 1 次请求）**：读两个 references，然后执行
   `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`
   输入 `/input`；输出 `work/corpus.md|json`、`work/meta.json`、`work/plan.json`、
   `work/review_model.json`（空骨架）、`/app/output/result.json`（已合法可解析）、
   `/app/output/report.md`。**此版已可解析，任何后续异常都不留半截产物。**
2. **通读与主张抽取（5–8 次请求）**：读 `work/corpus.md`，按 checklist 第 0/8 节建
   主张清单，区分 speaker，登记 `claims[]` 与引言 `commitments[]`，写入
   `review_model.json`。
3. **六维逐项审查（20–30 次请求）**：按 checklist 六维逐段核查；每条 finding 写
   `locator + quote + claim + evidence + reasoning + suggestion + evidence_refs`，
   套 severity-rubric 定 type/severity/status/confidence；再单独做一遍"承诺—交付"对照。
4. **增量写回（每攒够一批即执行）**：编辑完 `work/review_model.json` 后执行
   `python3 -B scripts/main.py update --output /app/output --work /app/work`
   输入 `review_model.json`；输出更新后的 `result.json`（脚本规范化、校验、记 change_log）、
   `evidence_map.json`、`plan.json`、`report.md`。**每 5 分钟至少写回一次。**
5. **终检与自检（最后 2–3 次请求）**：执行
   `python3 -B scripts/main.py selfcheck --output /app/output --work /app/work`
   退出码 0 即通过；修复它报出的问题，再以 `--status ok|partial|insufficient_evidence` 收尾。

时间纪律：第 1 步必须前 3 分钟落盘；若时间不足，宁可保持 `partial` 并如实写
`limitations`，也不宣称完成全文审查。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] 真在审查而非摘要：每条 finding 有 `locator`、逐字 `quote`、`evidence`、`reasoning`、
      可执行 `suggestion`。（实际效果·完成度/可核验性）
- [ ] 六类检查 `coverage` 全标注，且做了跨章节"承诺—交付"对照。（实际效果·完整性）
- [ ] 没把对照组、明确假设、转述观点误判为缺陷；speaker 已区分。（实际效果·正确性）
- [ ] 严重度有区分且 `reasoning` 写三问，不是一律"中"。（创新增益·可解释分级）
- [ ] `result.json` 可解析，含 `case_id` + output_contract 全部字段 +
      `status/sources/uncertainty/change_log`。（可靠体验·稳定性）
- [ ] `evidence_map` 每个 finding 都有可复核的段落指针与逐字 quote。（创新增益·可复核）
- [ ] 空输入/坏 JSON/坏编码/PDF 抽取失败均有降级且记录 `errors/degradations`，不崩溃。
- [ ] 长文截断、章节缺失、无法外部核验的引用均已写入 `limitations`/`pending_review`，
      未宣称全文已查。（可靠体验·异常处理）
- [ ] 建议都能落到具体句子（补什么证据、改哪句、如何降级结论强度）。（使用增益）
- [ ] `selfcheck` 通过且退出码 0；`selftest.py` 全部 PASS；重复运行内容一致（无随机性）。

## 7. 降级策略（缺数据/缺软件时）

- 缺材料、无文本层、PDF 抽取失败：先试平台 PDF/OCR 工具；仍失败则
  `status=insufficient_evidence`，`limitations` 写清缺什么、影响哪些检查项，**不编造**。
- 引用无法获取全文：该条标 `pending_review`，写"需外部核对"，不得断言"引用不支持"。
- 缺 `pdftotext`：`build_records` 自动记录 degradation，Agent 改用平台 pdf-text-tool
  后把文本补进 `work/corpus.md` 并同步 `corpus.json`。
- 参数缺失：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 写盘失败：`atomic_write_json` 用临时文件原子替换；仍失败则输出最小合法 result.json，
  不让进程崩溃。
- 任何未确认项归入 `uncertainty`/`pending_review`/`pending_human`；绝不伪造 DOI、数值、
  引用或软件状态。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 work/corpus.md，编辑 work/review_model.json 后：
python3 -B scripts/main.py update --output /app/output --work /app/work
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py   # 提交前自检，退出码 0 为通过
```

示意输出（标为示例）：`selfcheck` 打印 `selfcheck OK`；`selftest.py` 打印
`SELFTEST ALL PASS`；`result.json` 含上述全部字段。正式案例、白板与测评由平台提供，
本 Skill 不预置任何答案。
