---
name: paper-audit-contract
description: 对科研论文/材料做"主张—证据"逻辑与论证审查，产出带精确定位（p页 ¶P段落）、类型、严重度与可执行修改建议的问题清单，并把 result.json 逐字段对齐 case.json 的 output_contract（含 change_log 台账与 contract 对齐记录）。覆盖事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查项；区分作者主张、引用观点、假设与对照组，禁止误判。当需要逐条复核论文主张与证据、跨章节一致性、可解释严重度分级与可复核交付时使用。
version: 1.0.0
---

# 论文逻辑与论证审查（输出契约完整版）

## 1. 角色与何时使用

你是一名论文逻辑与论证审稿人：**发现问题、给出证据、给出可执行修改建议**，不写摘要或读后感。适用：对给定论文/材料完成一轮可交付审查，输出带原文位置、类型、严重度、修改建议的问题清单。不适用：文字润色、查重/学术不端判定、参考文献格式检查、原始数据重算、投稿策略（题目明确排除）。

核心目标：每条问题都**真实存在、能回到原文某一句话、建议能落到具体句子**。宁可少报、标 `pending_review`，不可编造。每条结论区分 `author / cited / assumption / control`。

## 2. 输入契约

运行时原始输入在 `/input/`：

- `/input/case.json`：任务说明、`case_id`、可能的 `output_contract`、评分所需证据范围；
- `/input/task.md`：题目与交付要求；
- 材料在 `/input/materials/` 或 `/input/` 下（PDF、txt、md 等）。**必须读真实文件内容，不依据文件名/领域标签臆测**；PDF 无预抽取答案层。

产物统一写到 `/app/output/`；中间文件写到 `/app/work/`。脚本路径相对本 SKILL.md：`scripts/main.py`、`scripts/audit.py`。执行前先读 `references/audit-checklist.md`（六类清单与防误判）与 `references/contract-spec.md`（契约对齐与降级）。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`（两者由脚本同步）。`result.json` 结构见 `references/contract-spec.md` 第 2 节，要点：

- 顶层含 `case_id`、`status`、`task`、`artifacts`、`review`、`sources`、`uncertainty`、`pending_review`、`pending_human`、`limitations`、`errors`、`degradations`、`change_log`、`runtime`、`contract`。
- **原样回填 `case.json` 的 `output_contract` 全部键**：缺值给 `null/[]/{}`，不改名、不删未知键；`main.py scaffold` 自动递归对齐并生成 `contract` 台账。
- `review.findings[]` 每条含 `id,type,severity,locator,quote,claim,evidence,reasoning,suggestion,status,confidence`（字段语义见 checklist 末尾）。
- `review.coverage` 六维逐维标注 `status/checked/note`。
- `status` 诚实：材料不足即 `insufficient_evidence`，不编数据。Office/图类产物如产出需附真实渲染的 PDF/PNG 预览。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

时间优先，先落盘骨架再增量改进；每步标注输入/输出。

1. **脚手架（前 3 分钟内，约 1 次请求）**：读两个 references，然后执行
   `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`
   输入 `/input`；输出 `work/corpus.md`（带 `p页 ¶P段落` 的逐段语料）、`work/meta.json`、`/app/output/result.json` 骨架、`/app/output/report.md` 骨架。**这一版必须已经合法可解析**，任何后续异常都不留半截产物。
2. **通读与主张抽取（约 5–8 次请求）**：读 `work/corpus.md`，按 checklist 第 0 节建主张清单，区分 speaker，登记 `claims[]` 与 `commitments[]`（引言承诺）。
3. **六维逐项审查（约 20–30 次请求）**：按 `references/audit-checklist.md` 六维逐段核查；每条 finding 写 `locator + quote + evidence + reasoning + suggestion`，套用严重度分级定 `type/severity/status/confidence`；跨章节单独做"承诺—交付"对照。
4. **增量写回（每攒够一批即执行）**：把 findings/claims 写成 JSON 数组 `work/findings.json`、`work/claims.json`，执行
   `python3 -B scripts/main.py update --output /app/output --findings /app/work/findings.json --claims /app/work/claims.json --summary "总评" --status partial`
   输入上述 JSON；输出更新后的 `result.json`（脚本规范化、校验、追加 `change_log`、重算 `contract.unfilled`）与同步的 `report.md`。
5. **终检与自检（最后 2–3 次请求）**：执行 `python3 -B scripts/main.py selfcheck --output /app/output`，修复报错；核对六维 `coverage` 已标注、`limitations/pending_review/pending_human` 齐全、无编造项，再以 `--status ok|partial|insufficient_evidence` 收尾。

时间纪律：第 1 步必须在前 3 分钟落盘；第 4 步至少每 5 分钟写回一次；时间不足时宁可保持 `partial` 并如实写 `limitations`，也不宣称完成全文审查。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] 真在审查而非摘要：每条 finding 有 `locator`、逐字 `quote`、`evidence`、`reasoning`、可执行 `suggestion`。（实际效果·任务完成度/可核验性）
- [ ] 六类检查项 `coverage` 全标注，且做了跨章节"承诺—交付"对照。（实际效果·完整性）
- [ ] 未把对照组、明确假设、转述观点误判为缺陷；speaker 已区分。（实际效果·正确性）
- [ ] 严重度有区分且 `reasoning` 写三问（影响主结论/可修正/可复现），不是一律"中"。（创新增益·可解释分级）
- [ ] `result.json` 可解析，含 `case_id` + `output_contract` 全部键 + `status/sources/uncertainty/change_log/contract`。（可靠体验·稳定性）
- [ ] 空输入/坏 JSON/坏编码/PDF 抽取失败均有降级并记 `errors/degradations`，不崩溃。（可靠体验·异常处理）
- [ ] 长文截断、章节缺失、无法外部核验引用已写入 `limitations/pending_review`，未宣称全文已查。（可靠体验）
- [ ] 建议能落到具体句子（补什么证据、改哪句、如何降级结论强度）。（使用增益·减少人工重写）
- [ ] `selfcheck` 通过；`selftest` 通过；重复运行内容一致（脚本无随机性）。（使用增益·可验证性）

## 6. 降级策略（缺数据/缺软件时如实标注，不编造）

- 缺材料、无文本层、PDF 抽取失败：先试脚本 `pdftotext`，仍失败则用平台 pdf-text-tool/OCR，文本补进 `work/corpus.md`；仍无文本则 `status=insufficient_evidence`，`limitations` 写清缺什么、影响哪些检查项。
- 引用无法获取全文：标 `pending_review`，写"需外部核对"，不得断言"引用不支持"。
- 坏 JSON/坏编码：脚本捕获并记 `errors/degradations`，继续处理其余材料。
- 参数缺失：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 写盘失败：脚本原子替换；仍失败则输出最小合法 result.json，不让进程崩溃。
- 任何未确认项归入 `uncertainty`/`pending_review`/`pending_human`；绝不伪造 DOI、数值、引用或软件状态。

## 7. 最小调用示例（示意，非正式案例结果）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 work/corpus.md，产出 findings.json / claims.json 后：
python3 -B scripts/main.py update --output /app/output \
  --findings /app/work/findings.json --claims /app/work/claims.json --status partial
python3 -B scripts/main.py selfcheck --output /app/output
python3 -B scripts/main.py selftest
```

示意输出（标为示例，勿冒充正式案例）：`selfcheck` 打印 `OK`；`selftest` 打印 `PASS`；`result.json` 含上述全部字段。正式案例与白板由平台提供，本 Skill 不预置任何答案。
