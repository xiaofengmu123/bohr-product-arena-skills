---
name: revision-response
description: >-
  审稿回复与论文修改（peer-review response & manuscript revision）：从 /input/case.json、
  /input/task.md 及 materials/ 的基线稿件与审稿意见摘要中，拆解每条意见及其子要求，
  逐条给出采纳/部分采纳/不采纳结论并把实质性修改真实落到可编辑修订稿，维护
  "意见—改动—定位—证据—版本"台账，产出修订稿、逐条回复信、修改定位与未到位证据清单，
  并写 /app/output/result.json（含 case_id、目标产物、事实与来源映射、版本与边界、
  status、sources、uncertainty、change_log）。适用于把一组审稿意见处理成可直接提交
  编辑部的修改稿与逐条回复的场景；不用于综述或纯问答。
version: 1.0.0
---

# 审稿回复与论文修改 Skill

## 1. 角色与何时使用
本 Skill 把"审稿意见清单 + 指定版本稿件 → 可编辑修订稿 + 逐条回复"沉淀为可复核流程，只解决四个问题：**答得全**（拆出每条意见的子要求）、**改得准**（"已修改"真实落在稿件且可定位）、**证得住**（补实验/数据/文献给出证据或说清为何无法补做）、**对得上**（回复—改稿—版本三者一致）。
触发：任务要求处理审稿回复、论文修改、意见回应或 revision letter，输入含 `/input/case.json` 与 `task.md`。
不做：补造输入外事实、伪造引用/DOI/实验、冒充真实审稿人逐字意见或编辑部已接收；意见摘要中的实质性修改必须真实写进演练基线，不能只声明"缺材料"跳过。

## 2. 输入契约
- 原始输入：`/input/case.json`（case_id、任务说明、材料路径、约束）、`/input/task.md`、`materials/manuscript-baseline.md`（组织方基线摘要）、`materials/reviewer-comments.md`（组织方意见摘要）。
- 按 case.json 给出的路径读实际文件；不按文件名臆测内容。文本用 `utf-8-sig` 容错；单文件 >2MB 跳过并记 `degradation`。
- 工作目录 `/app/work`，产物目录 `/app/output`；Skill 根 `/skills/weekly-skill`，脚本用相对路径调用。

## 3. 输出契约
必须产出 `/app/output/result.json`，字段：`case_id`、`deliverable`（目标产物）、`fact_source_mapping`（事实与来源映射）、`version_boundary`（版本与边界）、`status`、`sources`、`uncertainty`、`change_log`，另加结构化 `comments`（拆解后的意见）与 `revision_ledger`（改动台账）。前三项即题目 output_contract 全部字段，其余为诚实运行记录；schema 见 `references/elements.md`。
产物至少四件：`revised_manuscript.md`（修订稿）、`response_letter.md`（逐条回复信）、`revision_ledger.json`（意见—改动—定位—证据台账）、`evidence_checklist.md`（未到位证据清单）；docx/PPT 等另产真实渲染 PDF/PNG 预览。

## 4. 执行流程（预算 900 秒 / 64 次模型请求 / 单响应 16384 token）
硬性规定：**前 3 分钟内必须先落盘一版 result.json 骨架（status=in_progress/partial），之后只做增量改进，不整篇重写。**
- Step 0（0–1 min）读输入：`python scripts/scaffold.py --case /input/case.json --task /input/task.md --out /app/output --work /app/work`，立即写骨架并粗拆意见为 `comments`，列出材料缺口。
- Step 1（1–3 min）骨架落盘：写第一版 `revised_manuscript.md`（可先原样拷贝基线并标待改锚点），回填 `deliverable.path`。
- Step 2（3–6 min）意见拆解：每条意见 → `{id, 类型(方法/数据/写作/格式/伦理), 严重度, 子要求[], 决策(accept/partial/reject), 语气, 动作}`。
- Step 3（6–11 min）逐条落地：实质性修改真实写进修订稿，每条改动记 `revision_ledger`（意见id → 定位 → 原文/改后 → 状态）；需补实验/数据/文献的写 `evidence_checklist`（到位/未到位/替代说明）。
- Step 4（11–13 min）回复信：按 `references/elements.md` 模板逐条写 `response_letter.md`，语气四档（接受/澄清/礼貌反驳/申诉），每条带可检索定位锚点。
- Step 5（13–14 min）自检：`python scripts/selftest.py --result /app/output/result.json --out /app/output`，据报告修正。
- 之后每轮只改一处 → 更新 `fact_source_mapping`/`change_log` → 重跑 `selftest.py`；预留最后 30 秒最终验证并落盘。

## 5. 自检清单（逐条勾选，对应评分维度）
实际效果（40 分）：
- [ ] 每条意见及其全部子要求都有明确处理结论，无"只答一半 / 空致谢"。
- [ ] 每条"已修改"声明都能在 `revised_manuscript.md` 找到定位锚点，且与基线 diff 一致。
- [ ] 数字/术语/引用/关键论断逐条溯源 `fact_source_mapping`（traced/assumption/unverified），不新增材料外事实。
- [ ] 需补实验/数据/文献的意见有 `evidence_checklist`，无法补做时给出替代说明，不虚构"已补做"。
使用增益（25 分）：拆解 + 台账把"逐条比对意见、找行号、重写语气、补证据清单"压缩为一次结构化动作，可同输入复跑对照。
创新增益（15 分）：意见—改动—定位—证据四台账与版本一致性是基础 Agent 不易稳定实现的能力。
可靠体验（10 分）：长稿件/多意见/多轮/重复运行要素不漂移；意见冲突、意见有误、稿件缺章节、无法补实验、版本不匹配、权限不足被识别并处置。
生态贡献（10 分）：意见分类法、回复模板、定位脚手架、自检脚本可复用；使用说明与提交版本一致。

## 6. 降级策略
- 缺稿件/缺意见/坏 JSON/坏编码：`scaffold.py` 仍写合法 result.json（status=partial 或 insufficient_evidence），缺口记入 `version_boundary.material_gaps` 与 `uncertainty`，绝不编造。
- 意见本身有误/互相冲突/无法补实验/版本不匹配：记 `pending_human` + `anomalies`，正文用"澄清/礼貌反驳/申诉"语气，不硬改事实。
- `result.json` 写失败：确保 `/app/output` 存在后重试一次；仍失败保存 markdown 草稿并记录 `failed` 原因。
- 未经联网核验的公开链接只当"来源身份"，标 `unverified`，不冒充已核验全文或已录用状态。

## 7. 脚本（相对路径调用）
- `python scripts/scaffold.py --case /input/case.json --task /input/task.md --out /app/output --work /app/work`
- `python scripts/selftest.py --result /app/output/result.json --out /app/output`
规则、字段定义、意见分类法与回复模板见 `references/elements.md`，明细清单见 `references/checklist.md`。
