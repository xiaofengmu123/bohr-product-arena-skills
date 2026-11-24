---
name: revision-response
description: >-
  Turn reviewer comments plus a specified manuscript baseline into an editable
  revised manuscript, a point-by-point response letter, revision locators and an
  evidence list, keeping every claim, number and citation traceable to the input
  and maintaining a machine-checkable comment ledger. Use when the task is to
  revise a manuscript and answer reviewer comments, writing /app/output/result.json
  with case_id, the three output-contract fields, status, sources, uncertainty
  and a change log.
version: 1.0.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用

本 Skill 把「审稿意见清单 + 指定版本稿件」处理成可提交的修改稿与逐条回复信，核心目标是四个对齐：**答得全**（每条意见及其子要求都被识别并给出处置结论）、**改得准**（回复信声称的每处修改真实落在修改稿且可定位）、**证得住**（需补数据/文献/分析的条目有可查证据或无法补做的说明）、**对得上**（回复信、修改稿、版本台账三者一致）。

触发条件：任务要求处理审稿回复/论文修改，输入为 `/input/case.json` 与 `/input/task.md`。本 Skill 只做返修子场景（意见拆解 → 逐条回应 → 修改定位 → 证据清单 → 语气控制 → 版本一致性），**不整篇重写、不联网补实验、不冒称真实审稿人逐字要求、原作者已改稿或编辑部已接收**。若请求模糊或材料缺失，先列缺口再按显式假设推进，假设必须标注。

## 2. 输入契约

- 原始输入：`/input/case.json`（`case_id`、任务说明、材料路径、约束）与 `/input/task.md`（题目/体裁/格式约束）。
- 材料：按实际存在的文件读取，不按文件名臆测。本题材料为 `materials/manuscript-baseline.md`（组织方基线摘要）与 `materials/reviewer-comments.md`（组织方整理的意见摘要，非逐字全文）。PDF 用平台解析/渲染工具读原文。
- 读取要求：`utf-8-sig` 容错；单文件 >2MB 跳过并记录；坏 JSON/坏编码不崩溃。
- 工作目录 `/app/work`，产物目录 `/app/output`；Skill 根目录 `/skills/weekly-skill`。脚本用相对路径 `scripts/main.py` 调用。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段如下（前三项即题目 output_contract 全部字段）：

- `case_id`：从 case.json 读取，保持原值；缺失时回退到 task 中的 challenge id，仍无则 `"unknown"` 并记 uncertainty。
- `deliverable`（目标产物）：`{path, format, title, sections, status, previews}`；`path` 指向 `/app/output` 下可编辑源文件（markdown）；另写 `response-letter.md`（回复信）与 `revision-log.md`（修改台账），三者可编辑、可核对。
- `fact_source_mapping`（事实与来源映射）：数组，每项 `{claim, source_ref, location, quote, status}`；每条数字/术语/引用/关键论断至少一项，`status ∈ traced|assumption|unverified|source_conflict`，`traced` 必须带逐字 `quote`。
- `version_boundary`（版本与边界）：`{changes[], pending_confirmation[], material_gaps[], anomalies[]}`；`changes` 每项含 `comment_id`、动作、定位与状态，与台账一一对应。
- `response_ledger`（意见台账，本 Skill 核心结构化产物）：每项 `{comment_id, sub_requirements[], type, severity, disposition, tone, action, locator, evidence, status}`；`disposition ∈ accept|partial|reject`，`tone ∈ accept|clarify|rebut|appeal`。
- `status`：`in_progress|complete|partial|failed|insufficient_evidence|input_error`，最终为后五者之一。
- `sources`：数组 `{id, type, ref, description}`，`ref` 用 `/input` 内相对路径或可复核定位。
- `uncertainty`：数组 `{item, reason, disposition}`。
- `pending_human`：数组（需人工确认项汇总）。
- `change_log`：数组 `{step, action, detail, ts}`，每次落盘/修改追加一条。
- `runtime`：`{budget_seconds, budget_requests, schema_version, content_hash}`；`content_hash` 是对除 runtime 外全部字段的确定性指纹，供重复运行比对。

字段细则见 `references/elements.md`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，单次响应 ≤16384 tokens）

硬性规定：**前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进，不整篇重写**。

- **Step 0（0–1 分钟）读输入并落盘骨架**：`python scripts/main.py --case /input/case.json --task /input/task.md --out /app/output --work /app/work`。脚本发现输入、读 case_id 与约束、解析意见清单并预填 `response_ledger`，写 `status=in_progress` 的 result.json 骨架。
- **Step 1（1–2 分钟）补齐骨架字段**：人工/Agent 复核 `response_ledger` 的 comment_id 与子要求拆解；立即写第一版 `revised-manuscript.md` 到 `/app/output`，回填 `deliverable.path/format/title/sections`。
- **Step 2（2–6 分钟）逐条回应**：对每条意见判 `type/severity`、选 `disposition/tone`、写 `action`；把"已修改"落到正文并填 `locator`（节 + 段落 + 原文锚点 + 改后锚点）；需补证据的条目填 `evidence`（数据出处、到位状态、无法补做的替代说明）。
- **Step 3（6–11 分钟）修改稿与回复信**：依据台账动作逐处改稿；写 `response-letter.md`（逐条引用意见原文 → 决策 → 动作 → 定位 → 证据）与 `revision-log.md`；更新 `fact_source_mapping`、`version_boundary.changes`。
- **Step 4（11–13 分钟）自检**：`python scripts/selftest.py --input /input --out /app/output`，据报告修正「缺条目、定位找不到、证据无出处、版本对不上」之处。
- **Step 5（13–14 分钟）落盘与收尾**：重跑 selftest，修正后写 `status=complete` 或 `partial`，追加 `change_log`。
- 之后每轮只做增量改进：改一处 → 更新台账/映射 → 重跑 selftest；预留最后 30 秒最终校验与落盘。

## 5. 自检清单（逐条勾选，对应评分维度）

实际效果：

- [ ] 每条意见都进 `response_ledger`，一条意见内的多个子要求全部拆出并各有处置结论（答得全）。
- [ ] 每个 `accept|partial` 条目有具体 `action` 与 `locator`，改后锚点能在修改稿中被检索到（改得准/对得上）。
- [ ] 需补实验/数据/文献/重分析的条目有 `evidence`（出处 + 到位状态），无法补做时写明原因与替代方案（证得住）。
- [ ] `fact_source_mapping` 覆盖所有数字/术语/引用/关键论断，每条 `traced` 有逐字 `quote`，不新增材料外事实。
- [ ] 回复信、修改稿、`version_boundary.changes` 三者引用同一版本文档与同一批定位。

可靠体验：

- [ ] 缺材料/缺软件先写 `version_boundary.material_gaps` 与 `uncertainty`，再按标注假设推进，绝不编造。
- [ ] 意见冲突（同一指标两来源不一致）记录双方并标 `source_conflict`，不隐去。
- [ ] 重复运行要素不漂移：comment_id、台账条目数、周期/经费/格式约束前后一致。

生态/确定性：

- [ ] 产物为可编辑源文件（md），说明与提交版本一致；脚本纯标准库、相对路径可执行。
- [ ] `selftest.py` 全部 PASS（或如实记录 WARN/FAIL 及其影响）；`content_hash` 与重算一致。

## 6. 降级策略

- 缺数据/缺文献/缺软件：写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial` 并写明 limits；**绝不编造实验、数据、DOI 或软件状态**。
- 意见无法自动拆解：脚本退化为单条 `C1` 并记 uncertainty，Agent 手工拆分后覆盖台账。
- PDF/材料无法解析：如实记录，用可获得材料推进，相关条目在 `fact_source_mapping` 标 `unverified`。
- 无法补做实验/数据：列入 `pending_confirmation` 要求人工确认，回复信写"无法补做 + 替代说明"，不在正文硬填。
- 结果写失败：确保 `/app/output` 存在后重试一次；仍失败则保存 markdown 草稿并记录失败原因与退出的 `errors`/`degradations`。
- 意见本身有误/版本不匹配/权限不足：在 `anomalies` 中记录，语气选 `clarify|rebut` 并给出证据，不做无原则顺从或冒犯性表达。

## 7. 脚本（相对路径调用）

- `python scripts/main.py --case /input/case.json --task /input/task.md --out /app/output --work /app/work`（落盘骨架）
- `python scripts/selftest.py --input /input --out /app/output`（契约/台账/定位/证据/确定性自检）
- `python scripts/selftest.py --demo`（用临时合成 fixture 验证脚本可运行，退出码 0，不产生正式答案）

规则、字段与确定性别名见 `references/elements.md` 与 `references/checklist.md`。
