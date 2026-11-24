---
name: revision-response
description: >-
  Process reviewer comments against a manuscript baseline into a
  submission-ready revision package (审稿回复与论文修改). Decompose every
  comment into typed sub-requirements, decide accept / partial-accept /
  clarify / reject with a calibrated tone, draft a point-by-point response
  letter, land each accepted change in the manuscript with a searchable
  locator plus a verbatim before/after quote, and write /app/output/result.json
  carrying case_id, the three output-contract fields (deliverable,
  fact_source_mapping, version_boundary), status, sources, uncertainty,
  pending_human and a change log. Use when the task is reviewer response and
  paper revision, multi-round rebuttal tracking, or fact-preserving manuscript
  revision.
version: 1.1.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用
本 Skill 把“审稿意见清单 + 指定版本稿件基线”处理成可直接提交编辑部的返修包：
逐条拆解意见 → 逐条回应 → 修改定位 → 证据补充 → 语气控制 → 版本一致性。
触发条件：任务要求撰写/改写/校对/结构化审稿回复信或修订稿，输入为
`/input/case.json` 与 `task.md`，材料通常在 `/input/materials/` 下（如
`manuscript-baseline.md`、`reviewer-comments.md`）。若请求模糊、未给出
意见清单/稿件版本/受众/期刊格式/周期经费，先列缺失项写入
`version_boundary.material_gaps`，再按显式假设推进，假设必须标 `assumption`。
本 Skill 不补造输入之外的事实、不冒称真实审稿人逐字要求、不冒称已补做实验、
不伪造分数或“编辑部已接收”。

## 2. 输入契约
- 原始输入：`/input/case.json`（case_id、任务说明、材料路径、约束）与
  `task.md`（题目/体裁/格式约束）。先读两者原文，不按文件名臆测内容。
- 材料：按 case.json 给出的路径读取实际文件；PDF 用平台解析/渲染工具读原文。
- 单文件上限 2MB，超限跳过并记入 `degradations`；坏 JSON/坏编码逐条降级不崩溃。
- 工作目录 `/app/work`，最终产物 `/app/output`；Skill 根目录 `/skills/weekly-skill`。
- 脚本调用统一用相对路径 `python scripts/<name>.py`，执行前先
  `cd /skills/weekly-skill`（或在命令中给出该绝对前缀）。

## 3. 输出契约
必须产出 `/app/output/result.json`，字段如下（前三项即题目 output_contract 全部字段）：
- `case_id`：从 case.json 读取，保持原值，缺失时记 `unknown` 并列入 `pending_human`。
- `deliverable`（目标产物）：`{path, manuscript_path, format, title, sections, status}`；
  `path` 指向 `/app/output` 下的可编辑源文件（markdown/docx）。回复信与修订稿分开保存；
  docx/PPT/图表另产真实渲染 PDF/PNG 预览供检查。
- `fact_source_mapping`（事实与来源映射）：数组，每项
  `{claim_id, claim, source_ref, location, quote, kind, status, related_comment_id}`；
  每条数字/术语/引用/关键论断至少一项；`quote` 为来源逐字片段；`status`
  取值 `traced|assumption|unverified|not_reported|source_conflict`。
- `version_boundary`（版本与边界）：`{changes[], pending_confirmation[],
  material_gaps[], anomalies[]}`；每条 change 写清改前/改后、定位与状态。
- `comment_registry`：意见拆解台账，每项 `{comment_id, raw_quote, source_locator,
  sub_requirements[], decision, tone, response_summary, change_ids[]}`；
  `sub_requirements` 每条含 `{id, type, severity, action, status}`。
- `evidence_map`：修改定位与证据（本版强化重点），每项
  `{change_id, claim_id, related_comment_id, decision, kind, source_ref, locator,
  anchor, quote_before, quote_after, status, verified}`；每条“已修改”声明都必须
  `locator` + `quote_after`（逐字）可复核，`anchor` 为改稿后仍可检索的稳定片段。
- `sources`：`[{id, type, ref, description}]`，ref 用 `/input` 内路径或可复核定位。
- `uncertainty`：`[{item, reason, disposition}]`。
- `pending_human`：数组，需人工确认项（材料冲突、无法补实验、版本不匹配、指标不可达）。
- `change_log`：`[{ts, action, detail}]`，每次落盘/修改追加一条。
- 额外诚实字段：`status`（最终 `complete|partial|failed|insufficient_evidence|input_error`，
  禁用伪造；骨架期用 `in_progress`）、`errors[]`、`degradations[]`、`runtime`。
字段 schema 详见 `references/elements.md`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）
硬性规定：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进，不整篇重写。

- Step 0（0–1 分钟）读输入：`python scripts/scaffold.py --case /input/case.json
  --task /input/task.md --out /app/output/result.json`；读 case_id 与约束，再读
  task.md 与 materials 下全部材料，列出七要素所需输入是否齐全。
- Step 1（1–3 分钟）骨架落盘：scaffold.py 已写 `status=in_progress` 骨架；随后立即
  写第一版回复信与修订稿 markdown 到 `/app/output`，回填 `deliverable.path` 与
  `manuscript_path`，并逐条登记 `comment_registry`。
- Step 2（3–7 分钟）意见拆解与逐条回应：把每条意见拆成子要求（方法/数据/写作/格式/伦理），
  每条给决策（采纳/部分采纳/不采纳-澄清/不采纳-申诉）与语气（接受/澄清/礼貌反驳/申诉），
  把“已修改”落到具体动作。
- Step 3（7–11 分钟）修改定位与证据：每处改动写入 `evidence_map`，补
  `locator` + `anchor` + `quote_before/quote_after`；需补实验/数据/文献的生成证据
  清单，标明到位状态与无法补做时的替代说明；维护 `fact_source_mapping`。
- Step 4（11–13 分钟）自检：`python scripts/selftest.py --result
  /app/output/result.json --input /input`，按报告修正“答不全、改不准、证不住、对不上”。
- Step 5（13–14 分钟）落盘与验证：`python scripts/validate_output.py --result
  /app/output/result.json`，修正后写 `status=complete` 或 `partial`。
- 之后每轮只做增量改进：改一处 → 更新 `evidence_map`/`fact_source_mapping`/
  `change_log` → 重跑 selftest.py；预留最后 30 秒做最终 validate 与落盘。

## 5. 自检清单（逐条勾选，对应评分维度）
实际效果（40 分）：
- [ ] 每条意见及其子要求都被拆解并给出明确处理结论，无笼统致谢或只答一半。
- [ ] 每条“已修改”声明都有 `locator` + `quote_after`，且能在指定版本稿件中被找到。
- [ ] 需补实验/数据/文献的意见给可查证证据，或说明为何无法补做（列入 pending_human）。
- [ ] 回复信、修订稿、版本标记三者一致；每处定位独立可核验。
使用增益（25 分）：
- [ ] 提供可复跑的对照方式（同输入下加载/不加载 Skill 对比），而非仅文字自述。
创新增益（15 分）：
- [ ] `comment_registry`（拆解台账）+ `evidence_map`（逐字定位）为基础 Agent 难稳定实现的能力。
可靠体验（10 分）：
- [ ] 模糊请求先索要意见清单/稿件版本/受众/格式；缺材料先列 `material_gaps` 再标假设。
- [ ] 意见冲突、意见有误、稿件缺章节、无法补实验、版本不匹配、权限不足分别处置。
生态贡献（10 分）：
- [ ] 说明、依赖（仅标准库）、输入模板、示例、边界、维护信息齐全；脚本相对路径可执行。

## 6. 降级策略
- 缺数据/缺文献/缺软件：写入 `version_boundary.material_gaps` 与 `uncertainty`，
  `status=partial` 或 `insufficient_evidence`，写明 limits；绝不编造。
- PDF 无法解析：如实记录，用可获得材料推进，相关条目在 fact_source_mapping 标 `unverified`。
- 意见冲突或意见本身有误：两条都记录并标 `source_conflict`，写入 `pending_human`，
  不在正文硬填单方结论。
- 无法补实验/补数据：给替代说明（加强限定、补敏感性分析、补文献、明确未来工作），
  标 `not_reported` 并列入 `pending_confirmation`。
- result.json 写失败：确保 `/app/output` 存在后重试一次；仍失败则至少保存 markdown
  草稿并记录失败原因到 stderr。

## 7. 脚本与参考（相对路径调用，标准库）
- `python scripts/scaffold.py --case /input/case.json --task /input/task.md
  --out /app/output/result.json`
- `python scripts/selftest.py --result /app/output/result.json --input /input`
- `python scripts/validate_output.py --result /app/output/result.json`
- 规则与字段定义见 `references/elements.md` 与 `references/checklist.md`。
