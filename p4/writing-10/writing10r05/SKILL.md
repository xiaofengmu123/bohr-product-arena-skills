---
name: revision-responder
description: >-
  审稿回复与论文修改 Skill。从 /input/case.json 与 task.md 读取返修材料（审稿意见清单
  与指定版本稿件基线），完成意见拆解、逐条回应、修改定位、证据补充、语气控制与版本台账，
  交付可编辑修订稿、回复信草稿、修改定位表、证据清单和 /app/output/result.json（含 case_id、
  题目 output_contract 全部字段及 status/sources/uncertainty/pending_human/change_log）。
  当任务要求处理审稿意见、撰写回复信、修订稿件或维护多轮返修台账时使用。
version: 1.0.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用
本 Skill 把一次真实返修处理成可被独立核对的交付：**意见→决策→改动→定位→证据→语气**六列台账。
触发条件：任务要求处理审稿意见、撰写逐条回复信、修订指定版本稿件、或维护多轮返修台账；
输入为 `/input/case.json` 与 `task.md`（材料常位于 `/input/materials/`）。若意见清单或稿件
基线缺失，先在 `version_boundary.material_gaps` 列出缺口，再按显式假设推进，假设标 `assumption`。
本 Skill 不冒充真实审稿人逐字要求、不冒称补做了实验、不伪造已改稿或已接收状态、不编造分数。

## 2. 输入契约
- 原始输入：`/input/case.json`（case_id、任务说明、材料路径、约束）与 `task.md`（体裁/受众/格式约束）。
- 材料读取：按 case.json 给出的路径或 `/input/materials/` 目录实际读文件；`materials/manuscript-baseline.md`
  是基线摘要，`materials/reviewer-comments.md` 是意见摘要（均非完整原稿或审稿逐字全文）。PDF 用平台解析工具读原文，
  不得按文件名臆测内容。文本统一 `utf-8-sig` 读取并容错（BOM/坏编码/坏 JSON 降级，不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work`，产物目录 `/app/output`；Skill 根 `/skills/weekly-skill`，脚本用相对路径 `scripts/check_revision.py` 调用。

## 3. 输出契约（逐字段对齐，缺失给 null/[]，不删除未知键）
必须产出 `/app/output/result.json`。字段如下，前三项即题目 output_contract 全部字段：
- `case_id`：从 case.json 原值回填，不得改名。
- `deliverable`（题目“目标产物”）：`{revised_manuscript, response_letter, modification_map, evidence_ledger, format, status}`；
  四个子项各含 `{path, title, sections}`；path 指向 `/app/output` 下可编辑源文件（markdown/docx）；若体裁要求 docx/PPT/图表，
  另产真实渲染 PDF/PNG 预览。`status` 为 `draft|complete|partial`。
- `fact_source_mapping`（题目“事实与来源映射”）：数组，每项 `{claim, source_ref, location, quote, status}`；
  每条数字/术语/引用/关键论断至少一项，`quote` 为材料逐字片段，`status` ∈ `traced|assumption|unverified`。
- `version_boundary`（题目“版本与边界”）：`{changes[], pending_confirmation[], material_gaps[], anomalies[]}`；
  `changes[]` 每项 `{comment_id, action, location, status}`，是修改差异台账。
- `status`：`in_progress|complete|partial|failed`，最终为后三者之一；材料不足用 `partial` 并写 limits。
- `sources`：数组 `{id, type, ref, description}`，ref 用 `/input` 内路径或可复核定位。
- `uncertainty`：数组 `{item, reason, disposition}`。
- `pending_human`：数组 `{item, reason, who}`，收集需人工确认项（与 `version_boundary.pending_confirmation` 对应）。
- `change_log`：数组 `{ts, action, detail}`，每次落盘/修改追加一条。
- 另产出 `revision_ledger.json`（机器可读台账骨架，见 references/methodology.md 第 3 节字段）。
部分结果必须写清 limits；字段 schema 与逐条校验规则见 `scripts/check_revision.py` 顶部的 OUTPUT_CONTRACT。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 每响应 ≤16384 token）
硬性规定：**前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进，不整篇重写**。

- Step 0（0–1 分钟）读输入：`python scripts/scaffold.py --case /input/case.json --task /input/task.md --out /app/output --work /app/work`。
  读 case_id 与约束，再读 task.md，扫描材料文件，列出“意见清单、稿件基线、周期、受众、格式”是否齐全。
- Step 1（1–3 分钟）骨架落盘：scaffold.py 已写 `status=partial` 的 result.json 与五个交付骨架文件；立即回填
  `deliverable.*.path` 与 `sources`，追加第一条 `change_log`。此步产出即为可复核的最小合法结果。
- Step 2（3–6 分钟）意见拆解：逐条解析 reviewer-comments.md，按编号、类型（方法/数据/写作/格式/伦理/其他）、
  严重程度（critical/major/minor/editorial）、可执行动作拆分；一条意见含多个子要求时全部列出并标依赖关系。
- Step 3（6–10 分钟）逐条回应与修改：每条给决策 `accept|partial|decline|clarify|rebut`＋理由＋语气
  （接受/澄清/礼貌反驳/申诉），把“已修改”落到具体动作，写入 revised_manuscript.md、response_letter.md、
  modification_map.md，回填 ledger 的 `modifications[]` 与 `fact_source_mapping`。
- Step 4（10–12 分钟）证据补充：为需补实验/数据/文献/重做分析的意见生成 evidence_ledger.md，标到位状态
  （in_place|to_add|not_feasible|pending）、数据出处与无法补做时的替代说明；回填 ledger `evidence[]`。
- Step 5（12–13.5 分钟）自检：`python scripts/check_revision.py --result /app/output/result.json --out /app/output --case /input/case.json`，
  据报告修正“答不全、改不落、证不住、对不上”之处。
- Step 6（13.5–14.5 分钟）落盘：更新 `status=complete`（全部到位）或 `partial`（有缺口），追加 `change_log`。
- 之后每轮只做增量改进：改一处 → 同步 `fact_source_mapping`/`version_boundary`/`change_log` → 重跑 check_revision.py；
  预留最后 30 秒做最终 `python scripts/selftest.py`（退出码 0）与落盘。

## 5. 核心方法（对应四个考察问题）
- 答得全：意见拆解必须先穷举子要求再作答；回复信每条意见必须有明确处理结论，禁止笼统致谢或只答一半。
- 改得准：每处“已修改”必须在 `modifications[]` 中给 `{file, location, anchor, status}`；`anchor` 用
  “旧文本→新文本”或可检索原文锚点，check_revision.py 会校验新文本在稿、旧文本不在稿。
- 证得住：每项补证需求写 `{need, status, source, reason_if_infeasible}`；无法补做时如实说明原因与替代方案，不得编造数据。
- 对得上：回复信引用、modification_map、修订稿三者共用同一 `comment_id` 与同一 `anchor`；多轮返修把上一轮承诺
  作为新增约束重新过一遍，承诺变更须在 `change_log` 记录。

## 6. 自检清单（逐条勾选，对应评分维度）
实际效果（40 分）：
- [ ] 每条意见及其中每个子要求都有明确处理结论；无空泛致谢。
- [ ] 每处“已修改”声明与 revised_manuscript.md 真实状态一致，anchor 可被独立检索核对。
- [ ] 每条数字/术语/引用/关键论断有 `fact_source_mapping` 条目与逐字 quote，不新增材料外事实。
- [ ] 需补证意见有证据清单＋到位状态＋出处/无法补做说明；回复信、修改稿、版本三者一致。
可靠体验（10 分）：
- [ ] 缺材料先列 `material_gaps` 再按 assumption 推进；冲突信息双记并标 `source_conflict`。
- [ ] 重复运行要素不漂移：意见编号、决策、语气、anchor、周期前后一致；失败/异常分别说明。
- [ ] 空输入/坏 JSON/坏编码/缺文件均退出码 0 并写合法 result，不崩溃不编造。
使用/创新/生态（50 分）：
- [ ] 六列台账使意见拆解、定位、证据、语气、版本可独立验证，显著优于基础 Agent 的线性叙述。
- [ ] 提供可复跑对照：同输入下先跑 scaffold 得基线骨架，再加载本 Skill 得台账，diff 即增益证据。
- [ ] 交付可编辑源文件＋（Office/图另产）PDF/PNG 预览；脚本仅标准库、相对路径可执行；说明与提交一致。

## 7. 降级策略
- 缺意见/缺稿件/缺文献：写入 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，写 limits；绝不编造。
- 意见本身有误/互相冲突：礼貌反驳或澄清并双记 `source_conflict`；无法判断入 `pending_human`，不在正文硬填结论。
- 无法补实验/补数据：标 `not_feasible`，写原因与替代方案；不得冒称已补做。
- PDF 无法解析/版本不匹配/权限不足：如实记录到 `anomalies`，相关条目 `unverified`，用可得材料推进。
- result.json 写失败：确保 `/app/output` 存在后重试一次；仍失败则至少保留 markdown 草稿并记录失败原因。

## 8. 脚本与参考（相对路径调用）
- `python scripts/scaffold.py --case /input/case.json --task /input/task.md --out /app/output --work /app/work`
- `python scripts/check_revision.py --result /app/output/result.json --out /app/output [--case /input/case.json]`
- `python scripts/selftest.py`
字段定义见 `references/methodology.md`，自检清单见 `references/checklist.md`。
