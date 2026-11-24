---
name: reviewer-response
description: >-
  Turn a manuscript baseline and reviewer-comment list into an editable revision
  manuscript, a point-by-point response letter, a revision ledger (意见—改动—证据
  三栏台账) and an evidence list, keeping every claim, number and location traceable
  to the input with verbatim quotes and honest degradation for missing, conflicting
  or unverifiable items. Use when the task is to produce a review-response and
  revision deliverable plus /app/output/result.json carrying case_id, the three
  output-contract fields (deliverable, fact_source_mapping, version_boundary),
  status, sources, uncertainty and change_log.
version: 1.0.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用
本 Skill 把一份「审稿意见清单 + 指定版本稿件」处理成可直接提交的修订稿、逐条回复信、
三栏台账与证据清单。触发条件：任务要求撰写/改写/校对审稿回复、逐条响应意见、核对修改
定位或维护多轮返修一致性，输入为 `/input/case.json` 与 `task.md`。
本 Skill 不做综述堆砌，不补造输入之外的事实，不冒称真实审稿人逐字要求、真实补做实验、
原稿已改或编辑部已接收，不伪造分数。

## 2. 输入契约
- 原始输入：`/input/case.json`（case_id、任务说明、材料路径、约束）与 `task.md`（体裁/格式）。
- 本固定案例的命名材料为 `materials/manuscript-baseline.md`（基线摘要）与
  `materials/reviewer-comments.md`（意见摘要），是组织方摘要而非完整原稿/评审全文；公开链接
  是来源身份，不表示已联网核验。必须读实际文件，不得按文件名臆测。
- PDF 用平台解析/渲染工具读原文，不依赖预提取答案层。
- 工作目录 `/app/work`，最终产物目录 `/app/output`；Skill 根为 `/skills/weekly-skill`，脚本用相对路径调用。

## 3. 输出契约
必须产出 `/app/output/result.json`，含 `case_id` 与题目 output_contract 全部字段：
- `deliverable`（目标产物）：`{path, format, title, components, previews, status}`；components
  含 `revision_manuscript`、`response_letter`、`revision_ledger`、`evidence_list` 四件可编辑产物。
- `fact_source_mapping`（事实与来源映射）：数组，每项 `{claim, source_ref, location, quote, status}`；
  status ∈ `traced|assumption|unverified|source_conflict`。
- `version_boundary`（版本与边界）：`{changes[], pending_human[], material_gaps[], anomalies[]}`。
另加 `status`（`in_progress|complete|partial|insufficient_evidence|input_error|failed`）、
`sources[]`、`uncertainty[]`、`pending_human[]`、`change_log[]`（每次落盘追加一条）。
字段 schema 详见 `references/elements.md`。Office/PPT/图表另产真实 PDF/PNG 预览并写入
`deliverable.previews`；本案例为 markdown 基线，通常无需 Office 预览。

## 4. 核心方法：三栏台账（对得上的关键）
围绕四个评分问题（答得全、改得准、证得住、对得上），把每条意见拆成一行台账：
`comment_id / comment_quote / sub_requirements / type / severity / decision / rationale /
action / manuscript_location / evidence / tone / verification / cross_references`。
- 答得全：`sub_requirements` 拆出一条意见的多个子要求，逐项给结论，不笼统致谢、不只答一半。
- 改得准：`action` 是具体动作；`manuscript_location` 用「行号/章节/标题/原文片段」四选一，改稿后仍可解析。
- 证得住：`evidence.status ∈ {done, partial, not_done, impossible}`；非 done 必给 `fallback` 替代说明。
- 对得上：回复信由台账生成，`selftest.py` 复核回复信 ↔ 台账 ↔ 修订稿三者一致。
`tone ∈ {accept, clarify, polite_refute, appeal}` 控制语气强度，避免冒犯、过度承诺与无原则顺从。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求 / 每次响应 ≤16384 tokens）
硬性规定：**前 3 分钟内必须先落盘一版 partial 骨架，之后只做增量改进，不整篇重写。**

- Step 0（0–1 分钟）读输入：`python scripts/scaffold.py --case /input/case.json --task /input/task.md --out /app/output/result.json`。
  脚本读 case_id、发现材料、抽取意见编号并写 `result.json` 骨架与 `revision_ledger.json` 模板；
  坏 JSON/坏编码/空目录/超大文件均降级不崩溃。随后读 `task.md` 确认体裁、受众、格式。
- Step 1（1–3 分钟）补全台账：逐条读 `materials/reviewer-comments.md`，为每行填 `sub_requirements`、
  `type`、`severity`、`decision`、`rationale`、`tone`；有把握的先填，其余留 `null`。
- Step 2（3–6 分钟）逐条回应 + 改稿：为 accept/partial 意见写具体 `action`，并在
  `/app/output/revision_manuscript.md` 真实落修改，回填 `manuscript_location`。
- Step 3（6–11 分钟）证据清单：写 `/app/output/evidence_list.md`，为补实验/数据/文献/重分析意见标
  到位状态、数据出处与无法补做时的替代说明，同步到 `evidence`。
- Step 4（11–13 分钟）自检：`python scripts/selftest.py --result /app/output/result.json`，
  修正「答漏、定位找不到、证据无 fallback、语气越界」之处。
- Step 5（13–14 分钟）落盘与验证：`python scripts/validate_output.py --result /app/output/result.json`，
  修正后写 `status=complete` 或 `partial`（缺材料/未确认项则 partial）。
之后每轮只做增量改进：改一处 → 更新 `fact_source_mapping`/`change_log` → 重跑 selftest.py；
预留最后 30 秒做最终 validate 与落盘。

## 6. 自检清单（逐条勾选，对应评分维度）
实际效果：
- [ ] 意见全覆盖、子要求全覆盖；无笼统致谢、无只答一半。
- [ ] 每条 accept/partial 有具体 action；每条修改有 location 且能在修订稿中找到。
- [ ] 事实/数字/引用准确，逐条映射到 `source_ref` 与逐字 `quote`，不新增材料外事实。
- [ ] 语气分级正确，无冒犯、无过度承诺、无无原则顺从。
可靠体验：
- [ ] 缺材料先列 `material_gaps`；模糊请求先索要意见清单、稿件版本、期刊格式与权限。
- [ ] 空目录/坏 JSON/坏编码/缺参数/超大文件/写盘失败均优雅降级，不崩溃。
- [ ] 重复运行结果一致；意见冲突、意见有误、稿件缺章节、无法补实验、版本不匹配分别说明。
使用/创新/生态：
- [ ] 三栏台账使"对得上"可机检；定位四选一在改稿后仍有效；非 done 证据必给替代说明。
- [ ] 产物可编辑源文件 + 必要 PDF/PNG 预览；脚本仅标准库、相对路径可执行；说明与提交版本一致。
完整清单见 `references/checklist.md`。

## 7. 降级策略（如实标注，绝不编造）
- 缺数据/缺文献/缺软件：写入 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，
  写明 limits；不得编造补做实验、数据或软件状态。
- PDF 无法解析：如实记录，用可获得材料推进，相关条目在 `fact_source_mapping` 标 `unverified`。
- 意见互相冲突/意见本身有误/无法补实验/版本不匹配：列入 `pending_human` 要求人工确认，不在正文硬填。
- result.json 写失败：确保 `/app/output` 存在后重试一次；仍失败则至少保存 markdown 草稿并记录失败原因。
- 输入缺失或无法判断：`status=insufficient_evidence` 或 `input_error`，分别写 `errors`/`degradations`。

## 8. 脚本（相对路径调用，仅标准库）
- `python scripts/scaffold.py --case /input/case.json --task /input/task.md --out /app/output/result.json`
- `python scripts/selftest.py --result /app/output/result.json [--report /app/output/selftest.json]`
- `python scripts/validate_output.py --result /app/output/result.json`

## 9. 最小调用示例（示意，非正式案例）
```text
$ python scripts/scaffold.py --case /input/case.json --task /input/task.md --out /app/output/result.json
{"ok": true, "case_id": "<case_id>", "status": "in_progress", "comments_detected": 3, ...}
$ python scripts/validate_output.py --result /app/output/result.json
{"valid": true, "errors": 0, "warnings": 0, ...}
```
示意输出仅供参考，接口与完整流程以本文件为准。

## 10. 边界
本 Skill 只解决"审稿意见 → 修订稿 + 回复信 + 台账 + 证据清单"这一子场景，不覆盖所有学科与
期刊类型，不替代完整返修系统，不预写正式案例答案；没有真实材料时输出"材料不足"而非示例填充。
