---
name: thesis-writing-skill
description: 面向学位论文与长文档的科研写作/改稿 Skill：读取任务与科研材料、解析输出契约、三分钟内先落盘合法结果骨架，再执行章节规划/局部改写/校对，做跨章节术语、符号、编号、引用一致性检查与逐句证据溯源，产出可编辑稿件与可复核的 result.json。当需要依据固定科研材料完成学位论文或长文档的写作、改写、校对、引用补全与一致性维护时使用。
version: 1.0.0
---

# 学位论文与长文档写作 Skill

## 1. 角色与适用场景

本 Skill 让 Agent 依据给定科研材料完成长文档学术写作任务，交付"可编辑、可复核、可追溯"的产物，而不是给通用写作建议。适用：章节规划、局部改稿、术语/缩写统一、符号与编号规范、引用与论断绑定、跨章节一致性检查、版本改动记录。核心四个目标：

- **立得住**：章节规划与提纲、论证链条、字数分配对应，而非堆叠段落。
- **连得上**：跨章节术语、定义、符号、编号、图表引用、前后结论一致，改动一处能找到受影响段落。
- **改得动**：局部修改只动需要动的地方，保留作者原意，改动有差异与理由。
- **查得清**：每个论断/数字/引用/改动可回溯到原文、文献或版本记录。

不适用：一次性脚本、纯闲聊问答。无真实材料时输出"材料不足"，不编造数据与结论。

## 2. 输入契约

运行时工作根：`/app/work`。原始输入：

- `/input/case.json`：案例标识（case_id）、任务说明、`output_contract`（若存在，其字段必须全部回填）、材料清单与约束。
- `/input/task.md`：题目与运行契约（预算、评分要点）。
- `/input/organizer-edit-workorder.json`（若存在）：明确的编辑目标与离线本地工作范围；W16 提供的完整原文是**分节可编辑工作章节**，不是已完成的论文。
- `/input/` 下其余材料：章节 md、笔记、表格、PDF、图片等。**不假设固定文件名/键名**，先扫描发现再读取。PDF 用平台提供的 pdf-text-tool / pdf-render-tool 提取原文，不假设标签即内容。

读取规则：文本按 `utf-8-sig` 读取并容错（BOM/坏编码/坏 JSON 降级，不崩溃）；单文件默认 2MB 上限，超限跳过并在 `degradations` 记录。

## 3. 输出契约

最终产物写在 `/app/output/`，主产物：

- `result.json`（必需）：含 `case_id`、题目 `output_contract` 的**全部字段**（缺值用 `null`/`[]`/`{}`，不改名、不删未知键），并额外含 `status`、`sources`、`uncertainty`、`change_log`，以及 `evidence_map`、`artifacts`、`errors`、`degradations`、`pending_human`、`runtime`。
- 可编辑稿件：章节用 `.md`；结构化数据用 `.json`/`.csv`；术语表、符号表、引用表、改动记录用 `.md` 或 `.json`。
- 预览：若产出 Office/幻灯片/图，需用 office-renderer / 图表渲染出真实 PDF/PNG 预览，供检查。

字段语义（见 `references/result_format.md`）：

- `status` ∈ `ok | partial | insufficient_evidence | input_error`，必须诚实：材料不足写 `insufficient_evidence`，只完成部分写 `partial`。
- `sources`：材料与文献清单，每项含 `id`、`path`、`kind`、`role`。
- `uncertainty`：存疑/冲突/待确认项，每项含 `item`、`reason`、`impact`。
- `change_log`：每处改动含 `target`、`before`、`after`、`reason`、`locator`。
- `evidence_map`：每个关键结论/数值/引用/图元含 `claim`、`locator`、`quote`、`source_id`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 tokens）

**阶段 0：发现与骨架（0–3 分钟，≤4 次请求）。必须先落盘结果骨架。**

1. 运行 `python3 -B scripts/discover.py --input /input --output /app/output --work /app/work`：扫描输入、解析 case_id 与 output_contract、写 `result.json` 骨架（status=partial，全部契约字段给空值，空 sources/uncertainty/change_log）。
2. 读 task.md 与 organizer-edit-workorder.json，明确编辑目标、受众、文体、格式约束、证据范围。
3. 盘点材料：章节、笔记、文献、图表的清单与页码/行号定位。

**阶段 1：通读与规划（3–9 分钟，≤20 次请求）。**

4. 逐份读实际原文（必要时先 pdf-text-tool 抽取），建立"术语表、符号表、定义清单、结论清单、图表编号清单"。
5. 按提纲/编辑工单拆分可执行修改项，明确每章要回答什么、需要哪些证据、每处改动的原句与目标句。
6. 更新 `result.json` 的 `sources` 与 `pending_human`（把拿不准的项先挂起）。

**阶段 2：写作与改稿（9–13 分钟，≤30 次请求）。**

7. 逐项落地：生成/改写章节、统一术语与缩写、补/标存疑引用、重排编号与交叉引用。**只动需要动的段落**，保留作者原意与既有表述。
8. 每项改动同步写入 `change_log`（before/after/reason/locator），每个论断写 `evidence_map`（locator + 逐字 quote）。
9. 写入可编辑产物到 `/app/output/`，并随时保存增量版本（不要等到最后）。

**阶段 3：自检与定稿（13–15 分钟，≤10 次请求）。**

10. 运行 `python3 -B scripts/check_consistency.py --input /app/output --output /app/work/checks`，修复术语/符号/编号/交叉引用/重复定义问题。
11. 运行 `python3 -B scripts/check_evidence.py --evidence /app/work/evidence_map.json --base /input`，验证每条 evidence 的 locator 与 quote 真实存在；不通过的改写或标记 `pending_human`。
12. 定稿：填满契约字段，写 `uncertainty`/`errors`/`degradations`，把 `status` 改为最终诚实值，写 `runtime`（用时与请求数，从平台记录取，不编造）。

**任何阶段中断：** 已有 `result.json` 仍是合法 partial 产物；恢复后从第 10 步自检继续，不必重做。

## 5. 证据溯源与一致性规则（硬要求）

- 每个关键结论/数值/图元/引用必须有 `locator + quote` 逐字片段；冲突信息两条都记录并标 `source_conflict`，不隐藏。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。
- 术语同一概念只保留一个规范写法与唯一缩写；首次出现处定义，后续只引用缩写。
- 编号（章/节/图/表/式/参考文献）连续、无重复、无空引用；交叉引用指向真实存在的编号。
- 改动只动目标段落；`change_log` 让作者能看懂"改了什么、为什么改"。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] result.json 合法可解析，含 case_id 与 output_contract 全部字段（不缺键、不改名）
- [ ] status/sources/uncertainty/change_log/evidence_map/artifacts 齐全且诚实
- [ ] 章节按提纲/编辑工单真正写出或改出可用文本，非通用建议（任务完成度）
- [ ] 每个论断/数字/术语/编号与原文一致，引用与所引文献对得上（正确性）
- [ ] 提纲要求的章节、论证要素、图表与参考文献齐全（完整性）
- [ ] 每条结论可回溯到 locator + quote（可核验性）
- [ ] 术语表/符号表/编号/交叉引用经 check_consistency.py 通过（一致性）
- [ ] evidence 经 check_evidence.py 通过或明确挂起（溯源）
- [ ] 改动只动目标段落，change_log 含 before/after/reason（可编辑性）
- [ ] 空输入/坏 JSON/缺材料三种异常均不崩溃且如实标注（稳定性）
- [ ] 可编辑产物有对应 PDF/PNG 预览（若为 Office/图）
- [ ] 重复运行结果一致（确定性；见 references/checklist.md）

## 7. 降级与异常处理

- 缺数据/缺软件：如实写入 `limitations`/`degradations` 与 `status`，不编造软件状态或科研数据；缺关键材料 → `insufficient_evidence`。
- 坏 JSON/坏编码：`discover.py` 捕获并写 `errors`/`degradations`，继续处理其余材料，不崩溃。
- 材料缺失/冲突/无法判断/运行失败：分别写 `pending_human`/`source_conflict`/`uncertainty`/`errors` 并说明。
- 写盘失败：降级为最小合法 result.json（含 case_id、status、errors），不让进程崩溃。
- 超长/截断：单文件超限跳过并记录；长文档分批处理，用符号表与定义清单维持对前文的可见性。

## 8. 脚本（相对路径，仅标准库，务必能运行）

- `scripts/discover.py`：发现输入、解析契约、写 result 骨架。参数 `--input/--output/--work`，缺省 `/input`、`/app/output`、`/app/work`。
- `scripts/check_consistency.py`：章节一致性检查（术语/符号/编号/交叉引用/重复定义）。
- `scripts/check_evidence.py`：证据映射验证（locator 存在 + quote 逐字匹配）。

自检：先跑 `python3 -B scripts/discover.py --help` 退出码 0；再用空目录、坏 JSON 各跑一次验证降级。详细规则见 `references/result_format.md` 与 `references/checklist.md`。
