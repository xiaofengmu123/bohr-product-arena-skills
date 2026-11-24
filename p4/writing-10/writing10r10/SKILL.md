---
name: review-response-revision
description: >-
  Turn reviewer comments plus a specified manuscript baseline into a
  submission-ready revised manuscript, point-by-point response letter,
  revision-location map and evidence ledger. Splits each comment into
  sub-requirements, records accept/partial/reject/clarify/appeal decisions with
  a matching tone, anchors every claimed change to a stable before/after
  excerpt, and writes /app/output/result.json carrying case_id, deliverable,
  fact_source_mapping, version_boundary, status, sources, uncertainty and
  change_log. Use for reviewer-response and paper-revision tasks.
version: 1.0.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用
本 Skill 处理「审稿意见多、要求交叉、改动分散、回复信必须逐条对得上」的返修子场景。
输入为固定科研案例（任务说明 + 指定稿件基线 + 审稿意见摘要），输出可编辑修改稿、
逐条回复信、修改定位表与未到位证据清单，并对事实、数字、引用保留来源与修改记录。
触发条件：任务要求依据审稿意见修订稿件或撰写逐条回复。若请求模糊、未给出意见清单、
稿件版本、期刊格式或补实验条件，先列缺口写入 `version_boundary.material_gaps`，
再按显式假设推进，假设标注为 `assumption`。
本 Skill 只修订给定基线、逐条处理实际给出的意见，不冒称真实审稿人逐字要求、真实补做
实验、原作者已改稿、编辑部已接收或整篇全文返修完成。

## 2. 输入契约
- 原始输入：`/input/case.json`（`case_id`、任务说明、材料路径、约束）与 `task.md`。
- 材料：按实际文件读取 `materials/manuscript-baseline.md`（基线摘要）与
  `materials/reviewer-comments.md`（意见摘要）；`scripts/main.py` 自动发现材料，
  不假设固定文件名；PDF 用平台解析/渲染工具读原文，不从文件名臆测内容。
- 工作目录 `/app/work`，产物目录 `/app/output`；Skill 根目录为 `/skills/weekly-skill`。
- 公开论文/报告链接仅作「来源身份」，不表示已联网核验原评审全文。

## 3. 输出契约（/app/output/result.json）
必须产出 `/app/output/result.json`，含 `case_id` 与题目 output_contract 的三个字段：
- `deliverable`（目标产物）：`{primary, path, format, title, status, artifacts}`；四个
  可编辑子产物 `revised_manuscript`、`response_letter`、`revision_locations`、
  `evidence_ledger`，均为 markdown；docx/PPT/图表另产真实 PDF/PNG 预览。
- `fact_source_mapping`（事实与来源映射）：数组 `{claim, source_ref, location, status}`，
  每条数字/术语/引用/关键论断至少一项；`status` ∈ `traced|unverified|assumption`。
- `version_boundary`（版本与边界）：`{changes[], pending_confirmation[], material_gaps[],
  anomalies[]}`。
另含 `status`（`in_progress|complete|partial|insufficient_evidence|input_error`）、
`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`runtime`、
`change_log`（每次落盘追加一条）。字段 schema 见 `references/elements.md`。

## 4. 核心方法：台账优先（Ledger-first）
单一中间表示 `/app/work/revision_ledger.json` 是唯一事实来源，回复信、定位表、证据清单、
自动修改稿均从台账渲染，保证「对得上」。每条意见记录：
`id`、`ref`、`raw_text`、`sub_requirements[]`（含 `type`/`disposition`）、`decision`、
`tone`、`response`、`rationale`、`actions[]`、`locations[]`（`before`/`after`/
`anchor_status`）、`evidence[]`。
定位不使用易失效的页码行号，而用「改前逐字摘录 + 章节名 + 段序号」稳定锚点；
`check` 核验 `before` 在基线稿、`after` 在修改稿中同时命中，才标 `verified`。
校验顺序固定为覆盖→对齐→证据→语气，对应题目四问「答得全/改得准/证得住/对得上」。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求 / 16384 tokens/回复）
硬性规定：前 3 分钟内先落盘 `status=in_progress` 骨架，之后只做增量改进，不整篇重写。

- Step 0（0–1 分钟）发现输入：
  `python scripts/main.py scaffold --input /input --task /input/task.md --out /app/output --work /app/work`
  ；读 `case_id` 与约束，列出意见清单、稿件版本、期刊格式、补实验条件是否齐全。
- Step 1（1–3 分钟）骨架落盘：scaffold 已写 `result.json` 骨架与空台账；立即把
  `comments` 按意见编号/类型/严重程度拆成 `sub_requirements` 回填台账，写第一版
  `revised_manuscript.md` 草稿并回填 `deliverable.path`，落盘 `partial`。
- Step 2（3–9 分钟）逐条回应与修订：为每条意见定 `decision`/`tone`，把「已修改」落到
  具体动作与 `locations` 锚点，补齐 `evidence`；据此真实修改基线稿，产出可编辑
  `revised_manuscript.md`。
- Step 3（9–12 分钟）自检：
  `python scripts/main.py check --input /input --out /app/output --work /app/work`
  ；据检查报告修正「没答全、没改准、没证住、对不上」之处。
- Step 4（12–14 分钟）渲染与验证：
  `python scripts/main.py render ...` 生成回复信/定位表/证据清单/自动修改稿；
  `python scripts/main.py merge ...` 合成最终 `result.json` 并写 `status=complete`
  或 `partial`；运行 `python scripts/selftest.py`。
- 之后每轮只做增量改进：改一处台账 → 追加 `change_log` → 重跑 `check`；预留最后
  30 秒做最终 `merge` 与落盘。

## 6. 自检清单（逐条勾选，对应评分维度）
逐条见 `references/checklist.md`，关键项：四件套齐全；每条意见及子要求都有
`disposition`；每处「已修改」有 `before`/`after` 且 `anchor_status=verified` 的断言
可在稿件中独立复核；需补实验/数据的意见有 `evidence` 且 `status` 如实；回复信、修改稿、
台账三者一致；每条论断可定位到 `source_ref`，不新增材料外事实。

## 7. 降级策略
- 缺意见/缺稿件/缺补实验条件：写入 `version_boundary.material_gaps` 与
  `uncertainty`，`status=partial`，写明 limits；意见摘要中的实质性修改仍须真实落在
  指定基线，不得仅声明缺材料后跳过可执行修改。
- PDF 无法解析：如实记录，用可获得材料推进，相关条目标 `unverified`。
- 意见互相冲突/意见本身有误/无法补实验/版本不匹配/权限不足：列入 `pending_human`
  与 `anomalies`，说明替代方案，不在正文硬填。
- 空输入/坏 JSON/坏编码：脚本退出码 0，写合法 `result.json`（`insufficient_evidence`
  或 `input_error`），不崩溃、不编造软件状态或科学数据。

## 8. 脚本（相对路径调用，仅标准库）
- `python scripts/main.py scaffold --input /input --out /app/output --work /app/work`
- `python scripts/main.py check --input /input --out /app/output --work /app/work`
- `python scripts/main.py render --input /input --out /app/output --work /app/work`
- `python scripts/main.py merge --input /input --out /app/output --work /app/work`
- `python scripts/main.py all ...`（scaffold→check→render→merge 一键）
- `python scripts/selftest.py`
规则与字段定义见 `references/elements.md` 与 `references/checklist.md`。
