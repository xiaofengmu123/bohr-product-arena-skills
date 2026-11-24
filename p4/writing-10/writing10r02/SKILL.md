---
name: review-response-reviser
description: >-
  Turn reviewer comments plus a manuscript baseline into a submission-ready
  revised manuscript and point-by-point response letter. Decomposes each
  reviewer comment into sub-requirements with decision, type, severity and
  evidence needs; maps every revision to a locatable manuscript anchor; keeps an
  evidence ledger and four-level tone control; and verifies that the response
  letter, revised manuscript and version stay mutually consistent. Reads
  /input/case.json and task.md, writes /app/output/result.json with case_id,
  deliverable, fact_source_mapping, version_boundary, status, sources,
  uncertainty and change_log. Use when the task is reviewer-response or paper
  revision with a required point-by-point reply.
version: 1.1.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用
本 Skill 把"审稿意见清单 + 指定版本稿件基线"处理成可直接交编辑部的**修改稿 + 逐条回复信**，
并保留每条事实/数字/引用的来源与修改记录。核心只解决四件事，与题目四问一一对应：
**答得全**（每条意见及其多个子要求都有结论）、**改得准**（每处"已修改"真实落在正文且可定位）、
**证得住**（补实验/数据/文献类意见有证据或说清为何无法补做）、**对得上**（回复信、修改稿、版本三者一致）。
触发条件：任务要求"审稿回复 / 论文修改 / 逐条回应审稿意见"，输入为 `/input/case.json` 与 `task.md`。
本 Skill 不做整篇全文返修、不补造材料外事实、不冒称已补做实验或编辑部已接收；不做综述式堆砌。

## 2. 输入契约
- 原始输入：`/input/case.json`（读 `case_id`/`id`、约束、材料路径）与 `task.md`（体裁/格式/受众约束）。
- 材料发现：在 `/input` 下递归扫描 `.md/.txt/.json/.csv/.tex`，按文件名识别基线稿件
  （`manuscript`/`baseline`/`draft`/`paper`）与意见清单（`comment`/`review`/`reviewer`/`意见`），
  不按标签臆测内容；PDF 用平台解析/渲染工具读原文，读不到原文字层则记入 `material_gaps`。
- 约定：本固定案例的 `materials/manuscript-baseline.md` 与 `materials/reviewer-comments.md`
  是组织方**摘要**，不是完整原稿或审稿报告逐字全文；只修订该指定基线，逐条处理实际给出的意见。
- 工作目录 `/app/work`，最终产物 `/app/output`；Skill 根目录 `/skills/weekly-skill`，
  脚本一律用相对路径 `scripts/check_revision.py` 调用。

## 3. 输出契约（`/app/output/result.json`）
必须原样回填题目 output_contract 三个字段，再附运行状态字段，字段如下：
- `case_id`：从 case.json 读取，保持原值；读不到写 `null` 并在 `errors` 说明。
- `deliverable`（题目"目标产物"）：`{path, format, title, sections, status, revised_manuscript}`；
  `path` 指向回复信（主产物），`revised_manuscript` 指向修改稿；两者均为 `/app/output` 下可编辑
  markdown（docx/PPT/图表另产真实渲染 PDF/PNG 预览）。`sections` 列出回复信章节。
- `fact_source_mapping`（"事实与来源映射"）：数组，每项 `{claim, source_ref, location, quote, status}`；
  每条数字/术语/引用/关键论断/每处修改至少一项；`quote` 为材料或修改稿中的**逐字片段**，
  `location` 为定位表达式，`status` 为 `traced|assumption|unverified`。
- `version_boundary`（"版本与边界"）：`{changes[], pending_confirmation[], material_gaps[], anomalies[]}`；
  `changes` 逐条记 before/after 修改差异，`pending_confirmation` 记需人工确认项，`anomalies` 记冲突/异常。
- 附加字段：`status`（`in_progress|complete|partial|failed|insufficient_evidence|input_error`）、
  `sources[]`、`uncertainty[]`、`pending_human[]`、`errors[]`、`degradations[]`、`change_log[]`、
  `runtime{budget_seconds:900, budget_requests:64}`。字段定义见 `references/rubric.md`。

## 4. 执行流程：时间预算与早落盘（总 900 秒 / 64 次模型请求）
**硬性规定：前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进，不整篇重写。**

| 阶段 | 时间 | 请求 | 输入 | 输出（每步落盘） |
|---|---|---|---|---|
| P0 骨架 | 0–60 s | ≤4 | case.json + task.md | `python scripts/scaffold.py`：partial result.json（status=in_progress）+ `/app/work/comment_ledger.json` |
| P1 拆解 | 60–150 s | ≤6 | 台账 + 材料 | 逐条意见拆成台账（type/severity/decision/子要求），回复信与修改稿**首版**（所有意见 id 出现） |
| P2 成稿 | 150–480 s | ≤30 | 台账 + 首版 | 逐条回复（Decision→Action→Location→Evidence）+ 正文真实修改 + 证据/语气 |
| P3 自检 | 480–660 s | ≤10 | 全量 | `python scripts/check_revision.py`：按报告修正"漏项/定位失败/版本不一致" |
| P4 收尾 | 660–840 s | ≤10 | 全量 | 复核定稿、补证据清单、Office 产物渲染 PDF/PNG 预览 |
| P5 落盘 | 840–900 s | ≤4 | 全量 | `python scripts/selftest.py` 校验 schema，写终态 status 与 change_log |

- P0 即"早落盘"：scaffold.py 生成**合法** partial 骨架，任何中途崩溃都留下可复核产物，不是空目录。
- 之后每轮只做增量改进：改一处 → 更新 `fact_source_mapping`/`version_boundary`/`change_log` → 重跑 check_revision。
- 请求预算纪律：批式读文件（一次读完材料）、不重复读同一大文件、工具调用尽量并行、正文用 edit 增量改不整篇重写；
  预留最后 30 秒做最终校验，不把 64 请求用尽在反复重读上。

## 5. 自检清单（逐条勾选，对应评分维度）
**实际效果（40）**——答得全/改得准/证得住/对得上：
- [ ] 每条意见及其子要求都有一条台账记录，无漏项、无"只答一半"或笼统致谢。
- [ ] 每条意见有明确 decision（accept/partial/reject/clarify），拒绝/澄清给理由，不无原则顺从。
- [ ] 每条 accept/partial 的"已修改"落到具体动作 + locator + 逐字 anchor_quote。
- [ ] 每条定位能在当前版本修改稿中被独立检索到（check_revision.py 已通过 locators）。
- [ ] 补实验/数据/文献/重做分析类意见有 evidence_status，或写清"为何无法补做"的替代说明。
- [ ] 每条数字/术语/引用/关键论断可回溯到 source_ref + quote，不新增材料外事实。
**可靠体验（10）**：
- [ ] 意见冲突/意见有误/稿件缺章节/无法补实验/版本不匹配/权限不足均被识别并处置。
- [ ] 长稿件、多意见、多轮返修、重复运行下编号与定位不漂移，目标数量前后一致。
- [ ] 缺材料先写 `material_gaps` 再标注 `assumption` 推进；异常/失败分别说明，不编造。
**使用/创新/生态（35）**：
- [ ] 产出可编辑 markdown（或 docx）+ 回复信；Office 产物附真实 PDF/PNG 预览。
- [ ] 脚本纯标准库、相对路径可执行；SKILL.md 与提交版本一致。
- [ ] 增益有可复跑对照：`selftest.py`（交付前自检）与 `check_revision.py`（运行中自检），非文字自述。
- [ ] 意见分类法、回复信模板、定位脚手架、证据清单、语气红牌词可被他人复用。

## 6. 降级策略（绝不编造）
- 缺基线/缺意见/缺文献/缺软件：写入 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，写明 limits；
  意见摘要中的实质性修改仍须真实落在指定基线，不能仅以"缺材料"为由跳过可执行修改。
- 意见冲突：两条都记入 `version_boundary.anomalies` 并标 `source_conflict`，按更保守结论回复并请编辑/作者确认。
- 无法补做实验/数据：在回复信写"为何无法补做"（材料边界/时间/伦理/数据缺失）+ 替代说明（重分析/限制声明/敏感性分析），
  在 `fact_source_mapping` 标 `unverified`，不假装已补。
- PDF 无法解析：如实记录，用可获得材料推进，相关条目标 `unverified`。
- result.json 写失败：确保 `/app/output` 存在后重试一次；仍失败则至少保存回复信/修改稿草稿并记录失败原因。

## 7. 脚本调用（相对路径）
- `python scripts/scaffold.py --case /input/case.json --task /input/task.md --work /app/work --out /app/output/result.json`
- `python scripts/check_revision.py --result /app/output/result.json --work /app/work`
- `python scripts/selftest.py`（交付前自检，退出码 0 为通过）
规则、意见分类法、语气表、定位脚手架与回复信模板见 `references/rubric.md`。
