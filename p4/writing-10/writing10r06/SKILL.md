---
name: reviewer-revision
description: >-
  Turn reviewer comments and a specified manuscript version into an editable
  revised manuscript, a point-by-point response letter, revision locators and an
  evidence ledger, all traceable to the input. Works ledger-first: build the
  structured review_ledger.json, revision_map.json and evidence_ledger.json as a
  verifiable intermediate representation before writing prose, so every "revised"
  claim is anchored to a real before/after location. Writes /app/output/result.json
  with case_id, the three output-contract fields, status, sources, uncertainty,
  pending_human and a change log. Use when the task is to respond to reviewers and
  revise a manuscript.
version: 1.0.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用
本 Skill 处理“审稿意见清单 + 指定版本稿件”这一高频返修场景：把真实意见拆解为可执行动作，产出可编辑
修订稿、逐条回复信、修改定位台账与未到位证据清单，并保证“回复信—修改稿—稿件版本”三者一致。
触发条件：任务要求回复审稿意见、修改论文、写 rebuttal/response letter，或核对“已修改”声明与正文是否一致。
输入为 `/input/case.json` 与 `task.md`，材料为给定稿件版本与意见摘要。
边界：只修订给定基线，不补造输入之外的事实，不冒称真实补做实验、原作者已改稿或编辑部已接收，不伪造评分。
本 Skill 不覆盖完整审稿系统；仅解决“答得全、改得准、证得住、对得上”这一明确子问题。

## 2. 输入契约
- 原始输入：`/input/case.json`（case_id、任务说明、材料路径、约束；可能含 `output_contract`）与 `task.md`。
- 材料：按 case.json/task.md 给出的路径读取实际文件（常见为 `materials/manuscript-baseline.md`、
  `materials/reviewer-comments.md`）。读原文，不按文件名臆测；PDF 用平台解析/渲染工具读原文，不采信题面外摘要。
- 工作目录 `/app/work`（草稿），最终产物目录 `/app/output`；Skill 根目录 `/skills/weekly-skill`。
- 读取用 UTF-8（容忍 BOM），坏编码降级 latin-1；单文件 >2MB 跳过并记入 `degradations`。

## 3. 输出契约
必须产出 `/app/output/result.json`，字段如下（前三项即题目 output_contract 全部字段）：
- `case_id`：从 case.json 读取，保持原值；取不到则用材料中可识别的 id，仍无则 `unknown`。
- `deliverable`（目标产物）：`{path, format, title, sections, status}`；path 指向 `/app/output` 下可编辑
  markdown（修订稿与回复信），另附真实渲染 PDF/PNG 预览（如平台工具可用）。
- `fact_source_mapping`（事实与来源映射）：数组，每项 `{claim, source_ref, locator, quote, status}`；
  每条数字/术语/引用/关键论断至少一项，`status` 为 `traced|assumption|unverified`。
- `version_boundary`（版本与边界）：`{changes[], pending_confirmation[], material_gaps[], anomalies[]}`。
- `status`：`in_progress|complete|partial|failed|insufficient_evidence|input_error`，最终为后五者之一。
- `sources`：数组 `{id, type, ref, description}`；`pending_human`：数组 `{item, reason}`；
  `uncertainty`：数组 `{item, reason, disposition}`；`change_log`：数组 `{ts, action, detail}`，每次落盘追加。
- 可选 `errors` / `degradations` / `runtime`。部分结果必须写清 limits，字段 schema 详见
  `references/ledger_schema.md`。

## 4. 中间表示（本版核心：台账先行，改稿自证）
在写任何正文前，先建立三份结构化台账作为可复核的中间表示；回复信与修订稿由台账生成，因此“已修改”
声明必须能在台账中找到真实 before/after 定位，而不是空话。
- `review_ledger.json`（意见台账）：每条意见一个记录 `{comment_id, raw_quote, type, severity,
  sub_requirements[], decision, tone, response_summary, revision_refs[], evidence_refs[], status}`。
  `type ∈ method|data|writing|format|ethics|unknown`；`severity ∈ blocking|major|minor|unknown`；
  `decision ∈ accept|partial|reject|clarify|open`；`tone ∈ accept|clarify|polite_refute|appeal`。
  一条意见含多个子要求时，拆入 `sub_requirements[]`（`{sub_id, requirement, status}`），不得只答一半。
- `revision_map.json`（修改定位台账）：每处改动一个记录 `{change_id, comment_ids[], target, section,
  anchor, before_quote, after_quote, locator, status, note}`。`anchor` 为可检索的原文锚点（章节/段落/行号/
  首尾短语），`locator` 为“版本:章节:段落”定位；`status ∈ done|partial|not_done|blocked`。
- `evidence_ledger.json`（证据清单）：需补实验/数据/文献/重做分析的意见，每条 `{evidence_id,
  comment_ids[], kind, request, status, source_ref, fallback, note}`；
  `kind ∈ experiment|data|literature|analysis`；`status ∈ in_place|planned|not_possible|partial`；
  `not_possible` 必须写 `fallback`（说清为何无法补做及替代说明）。
生成脚本：`python scripts/main.py ledger --case /input/case.json --work /app/work --out /app/output`；
骨架先落盘，Agent 随后逐条回填，禁止整篇凭空生成。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求 / 每响应 16384 token）
硬性规定：**前 3 分钟内先落盘一版 `status=partial` 的 result.json 骨架与三份台账骨架**，之后只做增量
改进，不整篇重写。
- Step 0（0–1 分钟，≤3 req）：`python scripts/main.py scaffold --case /input/case.json
  --task /input/task.md --work /app/work --out /app/output`；读 case_id、约束、材料路径；列缺失项。
- Step 1（1–3 分钟，≤6 req）：读材料全文；`scripts/main.py ledger` 生成台账骨架，逐条填
  comment_id/raw_quote/type/severity/sub_requirements/decision。立即落盘 partial 结果。
- Step 2（3–6 分钟，≤12 req）：逐条回应：写 decision/tone/response_summary；接受或部分接受的修改登记为
  `revision_map` 条目（change_id、anchor、before/after quote、status）。
- Step 3（6–9 分钟，≤10 req）：补证据清单：写 `evidence_ledger`，标 in_place/planned/not_possible；
  not_possible 必写 fallback；冲突意见两个来源都记录并标 `source_conflict`。
- Step 4（9–12 分钟，≤8 req）：从台账生成 `/app/output/revised-manuscript.md` 与
  `/app/output/response-letter.md`；回复信每条引用 `CHG-x`/`E-x`，逐条对得上，语气按 tone 四档。
- Step 5（12–13.5 分钟，≤4 req）：`python scripts/main.py validate --out /app/output`；修正“对不上、
  定位缺失、证据缺失”；回填 `fact_source_mapping`。
- Step 6（13.5–14.5 分钟，≤2 req）：回填 `version_boundary`/`pending_human`/`uncertainty`/`change_log`，
  写 `status=complete` 或 `partial`，最终 validate。预留最后 30 秒只做增量与落盘。

## 6. 自检清单（逐条勾选，对应评分维度）
实际效果（40 分）：
- [ ] 每条意见及全部子要求都有明确处理结论，无“只答一半”或笼统致谢。
- [ ] 每条 accept/partial 有 revision_map 条目，before/after quote 可检索，locator 可在稿件中独立核对。
- [ ] 需补证据的意见有 evidence 条目；not_possible 写清 fallback；事实/数字/引用逐条映射到 source_ref。
- [ ] 回复信—修改稿—稿件版本三者一致；多轮返修中上一轮承诺仍成立（记入 version_boundary.changes）。
可靠体验（10 分）：
- [ ] 前 3 分钟已落盘 partial 骨架；缺材料先列 material_gaps 再标 assumption 推进，不编造。
- [ ] 空输入/坏 JSON/缺材料/版本不匹配/权限不足分别处理，status 诚实；重复运行结果一致。
使用/创新/生态（45 分）：
- [ ] 台账先行（中间表示）可复核；脚本仅标准库、相对路径可执行；产物可编辑 + PDF/PNG 预览。
- [ ] `scripts/selftest.py` 通过（空目录/坏 JSON/缺输入/正常路径/一致性校验五种场景）。

## 7. 降级策略
- 缺材料/缺文献/缺软件：写入 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，写 limits。
- PDF 无法解析：如实记录，用可得材料推进，相关条目 `fact_source_mapping` 标 `unverified`。
- 无法补实验/意见本身有误/版本不匹配/权限不足：写入 `pending_human` 或 `anomalies`，不在正文硬填；
  意见摘要中的实质性修改仍须真实落在指定基线，不得仅声明缺材料后跳过可执行修改。
- result.json 写失败：确保 `/app/output` 存在后重试一次；仍失败至少保存 markdown 草稿并记失败原因。

## 8. 脚本（相对路径调用，仅标准库）
- `python scripts/main.py scaffold --case /input/case.json --task /input/task.md --work /app/work --out /app/output`
- `python scripts/main.py ledger --case /input/case.json --work /app/work --out /app/output`
- `python scripts/main.py validate --out /app/output`
- `python scripts/selftest.py`（自检，不产生正式产物）
规则与字段定义见 `references/ledger_schema.md`，评分锚点见 `references/checklist.md`。
