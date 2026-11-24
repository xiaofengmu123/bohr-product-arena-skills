---
name: revision-response
description: >-
  Turn a reviewer-comment list plus a baseline manuscript into a verifiable
  point-by-point revision: a comment ledger, an editable revised manuscript, a
  response letter whose every "revised" claim maps to a diff with a searchable
  anchor, and an evidence checklist for supplement requests. Emphasizes explicit
  limitations and uncertainty so missing data is never fabricated. Writes
  /app/output/result.json with case_id, the three output-contract fields
  (deliverable, fact_source_mapping, version_boundary), status, sources,
  uncertainty, pending_human and change_log. Use when the task is to respond to
  peer review or revise a manuscript from /input/case.json and task.md.
version: 1.0.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用
本 Skill 把"审稿意见清单 + 指定版本稿件"处理成可直接提交的修改稿、逐条回复信与可核验台账，重点解决四个问题：答得全、改得准、证得住、对得上。
触发条件：任务要求回复审稿意见、修改稿件、校对或结构化写作，输入为 `/input/case.json` 与 `/input/task.md`。
若材料只有组织方基线摘要与意见摘要（非完整原稿、非审稿报告逐字全文），必须只修订给定基线，并显式标注缺口；不得冒称真实审稿人逐字要求、真实补做实验、原作者已改稿或编辑部已接收。
本 Skill 不做综述堆砌，不新增材料外事实，不伪造实验数据、引用或运行结果。

## 2. 输入契约
- 原始输入：`/input/case.json`（case_id、任务说明、材料路径、约束）与 `/input/task.md`（题目/体裁/格式约束）。
- 材料：按 case.json 给出的路径读取实际文件；优先识别 `materials/manuscript-baseline.md`（基线）与 `materials/reviewer-comments.md`（意见摘要）。PDF 用平台 PDF 工具读原文，不得从文件名或领域标签臆测内容。
- 工作目录 `/app/work`，最终产物目录 `/app/output`；Skill 根目录为 `/skills/weekly-skill`，脚本用相对路径 `scripts/*.py` 调用。
- 文本按 `utf-8-sig` 容错读取；单文件超过 2MB 跳过并写入 `degradations`。

## 3. 输出契约
必须产出 `/app/output/result.json`，字段如下（前三项即题目 output_contract 全部字段）：
- `case_id`：从 case.json 原样读取，保持原值。
- `deliverable`（题目字段 1「目标产物」）：`{path, format, title, version, sections, status}`；path 指向 `/app/output` 下可编辑源文件（markdown/docx）；Office 产物另产真实 PDF/PNG 预览。
- `fact_source_mapping`（字段 2「事实与来源映射」）：数组，每项 `{claim_id, claim, kind, source_ref, location, quote, status}`；status ∈ `traced|derived|assumption|unverified|source_conflict`；`traced` 必须带逐字 quote，`assumption` 必须同步进 `pending_human` 或 `uncertainty`。
- `version_boundary`（字段 3「版本与边界」）：`{changes[], pending_confirmation[], material_gaps[], anomalies[]}`；changes 每项 `{id, comment_id, before, after, location, anchor, manuscript_version, status}`；`anchor` 是改后稿中可检索的 8–40 字唯一片段。
- `response_letter`：`{path, entries[]}`；entry 每项 `{comment_id, decision, tone, summary, subrequirements[], change_ids[], evidence[], unresolved[]}`；decision ∈ `accept|partial-accept|clarify|rebut|appeal`，tone ∈ `accept|clarify|rebut|appeal`。
- `status`：`in_progress|partial|complete|failed|insufficient_evidence`，最终为后四者之一。
- `sources`：`{id, type, ref, description}`；`uncertainty`/`pending_human`：`{item, reason, disposition}`；`limitations`：`{item, scope, impact}`；`degradations`：`{item, cause, fallback}`。
- `change_log`：`{ts, action, detail}`，每次落盘/修改追加一条。
字段与枚举定义详见 `references/ledger_schema.md`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 16384 token 每响应）
硬性规定：前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进，不整篇重写。

- Step 0（0–1 分钟）读输入：`python scripts/scaffold.py --case /input/case.json --task /input/task.md --materials /input/materials --out /app/output/result.json --work /app/work`；确认 case_id、意见条数、基线文件是否存在。
- Step 1（1–3 分钟）骨架落盘：scaffold.py 已写 `status=in_progress` 骨架；随后立即写第一版回复信骨架与修改稿骨架到 `/app/output`，回填 `deliverable.path` 与 `response_letter.path`。
- Step 2（3–8 分钟）意见拆解与台账：逐条把意见拆成子要求，标注类型（方法/数据/写作/格式/伦理）、严重程度、决策与语气；同一意见内多个子要求必须全部拆出，不得只答一半。
- Step 3（8–11 分钟）逐条回应与修改定位：每条采纳/部分采纳落到具体动作与 diff（before→after+anchor）；需要补实验/数据/文献的生成证据清单，标明到位状态与出处，无法补做时给出替代说明；语气区分接受、澄清、礼貌反驳、申诉。
- Step 4（11–13 分钟）版本一致性自检：`python scripts/check_revision.py --result /app/output/result.json --manuscript /app/output/<改稿>.md --response /app/output/<回复信>.md`，据报告修正"对不上、查不到、证不住"处。
- Step 5（13–14 分钟）落盘与终验：写 `status=partial` 或 `complete`，重跑 check_revision.py；最后 30 秒只做最终 validate 与落盘。
- 之后每轮只做增量改进：改一处 → 更新 `fact_source_mapping`/`version_boundary`/`change_log` → 重跑 check_revision.py。

## 5. 自检清单（逐条勾选，对应评分维度）
实际效果（40 分）：
- [ ] 意见全覆盖：每条意见及其全部子要求都有明确处理结论，无笼统致谢、只答一半或答非所问。
- [ ] 每条"已修改"落到真实 diff（before→after+anchor），anchor 能在指定版本稿件中被独立检索到。
- [ ] 补实验/数据/文献的意见有证据清单（到位状态+数据出处）或"无法补做"的替代说明。
- [ ] 回复信、修改稿、稿件版本三者一致；多轮返修中上一轮承诺在台账中仍可查。
- [ ] 每条数字/术语/引用/关键论断可回溯到 `source_ref` + `quote`，不新增材料外事实。
可靠体验（10 分）：
- [ ] 缺材料先列 `material_gaps`/`limitations`，再标注 assumption 推进；模糊请求先索要稿件版本/格式/受众。
- [ ] 重复运行时要素不漂移，意见编号、版本号、目标数量前后一致。
- [ ] 意见互相冲突/意见有误/稿件缺章节/无法补实验/版本不匹配分别说明，不编造软件状态或科学数据。
使用/创新/生态：
- [ ] 产出可编辑源文件 + PDF/PNG 预览；脚本仅标准库、相对路径可执行；`scripts/selftest.py` 可复跑证明脚本有效。

## 6. 降级策略与不确定性（本版重点）
- 缺数据/缺全文/缺软件：写入 `version_boundary.material_gaps`、`limitations`、`uncertainty` 与 `pending_human`，`status=partial`，写明 limits；绝不编造实验数据、引用或软件状态。
- 意见摘要非审稿全文：不冒称审稿人逐字要求；但摘要中的实质性修改仍须真实落在给定基线，不能仅声明缺材料后跳过可执行修改。
- 无法补实验：用礼貌澄清或申诉，说明"需要何种证据才能补做"，标 `pending_human`，不在正文硬填"已补做"。
- 来源冲突：同一指标两处来源不一致时两处都记录并标 `source_conflict`，不隐藏不折中。
- 定位在改稿后失效：anchor 必须是改后仍可检索的唯一片段；改稿重排导致 anchor 失效时，重跑 check_revision.py 发现并重新锚定。
- result.json 写失败：确保 `/app/output` 存在后重试一次；仍失败则至少保留 markdown 草稿并记录失败原因到 `degradations`。

## 7. 脚本调用（相对路径，仅标准库）
- `python scripts/scaffold.py --case /input/case.json --task /input/task.md --materials /input/materials --out /app/output/result.json --work /app/work`
- `python scripts/check_revision.py --result /app/output/result.json [--manuscript /app/output/<改稿>.md] [--response /app/output/<回复信>.md]`
- `python scripts/selftest.py --work <临时目录>`
规则与字段见 `references/ledger_schema.md` 与 `references/checklist.md`。

## 8. 边界
本 Skill 只解决"审稿回复与论文修改"一个返修子场景，不覆盖所有学科/期刊类型。无真实材料时输出 `insufficient_evidence` 而非示例填充。核心创新取舍：以"可检索 anchor + diff 台账 + 证据清单 + 显式语气字段"实现"改得准、证得住、对得上"，把语气控制做成结构化字段而非自由文本，把不确定性与降级做成一等公民字段而非脚注。
