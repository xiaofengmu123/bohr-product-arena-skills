---
name: reviewer-response
description: >-
  Turn a reviewer-comment list and a manuscript baseline into a point-by-point
  response letter, an actually-revised editable manuscript and a
  version-consistent revision ledger, so every accepted/partial decision lands
  as a locatable text change and every evidence item is marked done or
  cannot-do. Use when the task is peer-review response and paper revision
  (审稿回复与论文修改): decompose comments, decide accept/partial/reject with tone
  control, apply revisions to the manuscript, and write /app/output/result.json
  carrying case_id, the three output-contract fields (deliverable,
  fact_source_mapping, version_boundary), status, sources, uncertainty and a
  change log.
version: 1.0.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用
本 Skill 处理"审稿回复与论文修改"：从给定审稿意见清单与指定版本稿件出发，产出逐条回复信、真实落在稿件中的修订、可定位的修改记录与未到位证据清单，并保证回复信、修改稿与版本台账三者一致。核心目标是四问全过：**答得全、改得准、证得住、对得上**。
触发条件：任务要求处理审稿意见/回复审稿人/修订论文，输入含 `/input/case.json`、`task.md` 与 `materials/`。只修订给定基线；不冒称真实补做实验、原作者已改稿、编辑部已接收或整篇全文返修完成。若输入是普通写作任务而非返修，不适用本 Skill。

## 2. 输入契约
- 原始输入：`/input/case.json`（case_id、任务说明、约束）、`/input/task.md`（题目/体裁/格式）、`materials/manuscript-baseline.md`（基线摘要）、`materials/reviewer-comments.md`（意见摘要）。
- 材料按 case.json / 目录实际路径读取；PDF 用平台解析/渲染工具读原文，不得从文件名或领域标签臆测内容。
- 工作目录 `/app/work`，最终产物目录 `/app/output`；Skill 根目录 `/skills/weekly-skill`，脚本以相对路径 `scripts/main.py` 调用。
- 意见摘要是组织方整理摘要而非审稿逐字全文；实质性修改仍须真实落在指定基线中，不能仅声明缺材料后跳过可执行修改。

## 3. 输出契约
必须产出 `/app/output/result.json`，字段如下（前三个即题目 output_contract 全部字段）：
- `case_id`：从 case.json 读取，保持原值。
- `deliverable`（目标产物）：`{revised_manuscript, response_letter, revision_ledger}`，每项 `{path, format, status}`；path 指向 `/app/output` 下可编辑源文件（markdown/json）。
- `fact_source_mapping`（事实与来源映射）：数组 `{claim, source_ref, location, quote, status}`；每条数字/术语/引用/关键论断至少一项，status ∈ `traced|revised|assumption|unverified`。
- `version_boundary`（版本与边界）：`{version_id, changes[], pending_confirmation[], material_gaps[], anomalies[]}`。
- `status`：`in_progress|complete|partial|insufficient_evidence|input_error|failed`；最终为后五者之一。
- `sources`：数组 `{id, type, ref, description}`，ref 用 `/input` 内路径或可复核定位。
- `uncertainty`：数组 `{item, reason, disposition}`。
- `change_log`：数组 `{ts, action, detail}`，每次落盘/修改追加一条。
- `errors` / `degradations`：记录解析与运行异常，不掩盖、不编造。
台账 `/app/output/revision-ledger.json` 是"对得上"的单点真相，每条意见一个 entry：

```json
{"comment_id":"R1-C1","comment_type":"method","severity":"major",
 "sub_requirements":["…"],"decision":"accept|partial|reject",
 "tone":"accept|clarify|rebut|appeal","response_summary":"…",
 "changes":[{"action":"…","section":"Methods","before":"…","after":"…",
 "anchor":"短引文","status":"done|pending"}],
 "evidence":{"needed":true,"status":"done|partial|cannot_do|planned",
 "source":"…","cannot_do_reason":"…"},"status":"resolved|pending|rejected"}
```

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 16384 token）
硬性规定：**前 3 分钟内先落盘 partial 结果骨架，之后只做增量改进，不整篇重写**。

- Step 0（0–1 分钟）scaffold：`python scripts/main.py --input /input --output /app/output --work /app/work`。读 case_id、意见、基线，自动拆意见为台账骨架、复制基线为修订稿、按意见预置回复信标题，写 `result.json`（`status=partial`）。
- Step 1（1–3 分钟）首版落盘：给每条意见补决策/语气/子要求，写首版 `response-letter.md` 与 `revised-manuscript.md`，回填 `deliverable` 与 `fact_source_mapping` 首条。
- Step 2（3–6 分钟）意见拆解与证据审计：按类型（方法/数据/写作/格式/伦理）、严重度、子要求拆意见；标每条 `evidence.needed` 与到位状态。
- Step 3（6–11 分钟）逐条回应与真实修订：写回复信正文，把每个"已修改"落到稿件具体段落，用 `before`/`after`/`anchor` 记录定位，可执行修改必须真实改入基线。
- Step 4（11–13 分钟）四问自检：`python scripts/check_revision.py --ledger … --manuscript … --response … --result …`，据报告修正对不上、改不准、答不全之处。
- Step 5（13–14 分钟）落盘与验证：`python scripts/selftest.py --validate /app/output/result.json`，修正后写 `status=complete` 或 `partial`，预留最后 30 秒收尾。
- 之后每轮只做增量改进：改一处 → 追加 `change_log` → 重跑 `check_revision.py`；不整篇重写。

## 5. 自检清单（逐条勾选，对应评分维度）
实际效果（40 分）：
- [ ] 答得全：每条意见及全部子要求均有明确决策与回应，无笼统致谢或只答一半。
- [ ] 改得准：每个 accept/partial 的"已修改"声明在稿件中有可定位 anchor 且真实落地。
- [ ] 证得住：需补实验/数据/文献/重分析的意见有证据清单，注明到位状态或无法补做原因。
- [ ] 对得上：回复信、修改稿、版本台账三者一致；多轮返修上一轮承诺仍成立。
可靠体验（10 分）：
- [ ] 冲突意见、意见本身有误、稿件缺章节、无法补实验、版本不匹配分别识别与处置。
- [ ] 重复运行要素不漂移；缺材料先列 `material_gaps` 再标假设推进；异常如实记录。
使用/创新/生态（15+25+10 分）：
- [ ] 意见拆解/锚点定位/语气分级/台账比通用写作 Agent 更稳，边界写明。
- [ ] 脚本仅标准库、相对路径可执行；提供空输入/坏 JSON/最小案例自检。
- [ ] 意见分类法、回复信模板、定位脚手架、评分锚点可被他人直接复用。

## 6. 降级策略
- 缺意见/缺基线/缺章节/缺数据/缺文献/缺软件：写入 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，写明 limits；绝不编造实验、数据或软件状态。
- PDF 无法解析：如实记录，用可获材料推进，相关条目在 `fact_source_mapping` 标 `unverified`。
- 无法补实验/指标不可达/意见相互冲突/意见本身有误：列 `pending_confirmation` 要求人工确认，不在正文硬填；礼貌反驳或申诉须有证据，不无原则顺从。
- `result.json` 写失败：确保 `/app/output` 存在后重试一次；仍失败则至少保存 markdown 草稿并记录失败原因。

## 7. 脚本（相对路径调用）
- `python scripts/main.py --input /input --output /app/output --work /app/work`
- `python scripts/check_revision.py --ledger /app/output/revision-ledger.json --manuscript /app/output/revised-manuscript.md --response /app/output/response-letter.md [--result /app/output/result.json]`
- `python scripts/selftest.py [--tmp <dir>] [--validate /app/output/result.json]`
字段定义与意见分类法见 `references/method.md`，评分维度对齐见 `references/checklist.md`，回复信/台账模板与最小调用示例见 `references/templates.md`。
