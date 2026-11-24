---
name: research-to-impact-writing
description: 科研成果转化写作 Skill：把论文摘要、技术报告、实验材料等科研底稿，按目标读者与体裁改写为可直接使用的科普稿、项目申报材料、技术报告或改稿，并同步产出可逐条回溯的事实/来源映射、术语锁定表与逐段改动清单。当需要做受众适配、内容取舍、文体转换且必须保留事实、引用与论据边界时使用。
version: 1.0.0
---

# 科研成果转化写作 Skill

## 1. 角色与使用时机

本 Skill 把"科研底稿 → 面向特定读者的可用稿件"这一流程固化下来。它只做一件事：完成一次**可直接提交的成果转化改写**，而不是给提纲、写作建议或片段润色；同时保留来源，让人能按段复核。

适用场景：论文摘要改科普、技术报告改项目申报、学术稿改决策者简报、双语/结构化改写、术语统一与校对。不适用：纯问答、一次性脚本、无底稿的命题作文。

四条验收主线（贯穿全部步骤）：**读得懂**（不解释不清的缩写、公式、行话）、**留得住**（关键结论/数据/限定/样本/口径/图表编号不丢，删冗余不删证据）、**说得准**（论断强度与源文匹配，不把相关写因果、不把初步写成定论）、**接得住**（每处删改有清单与理由，可逐段回溯）。

## 2. 输入契约

运行时原始输入固定为：

- `/input/case.json`：案例主文件，含 `case_id`、任务说明、目标读者、体裁、篇幅等约束，可能含 `output_contract`（或 `output`/`outputs`/`required_fields`）字段；**必须原样回填其中全部键**。
- `/input/task.md`：本题说明。
- 科研材料在 `/input/` 或其子目录中（`materials/`、`*.pdf`、`*.md`、`*.txt`、`*.docx` 等），**文件名不可假设**，先扫描再读取。

工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

读取规则：`utf-8-sig` 优先，坏编码/坏 JSON 降级不崩溃；单文本材料默认 2MB 上限，超限跳过并记录到 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段如下：

```json
{
  "case_id": "<case.json 的 case_id>",
  "<output_contract 的每个键>": "<原样回填，未完成给 null 或 []>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "artifacts": { "manuscript": "相对路径", "evidence_map": "相对路径", "change_log": "相对路径" },
  "sources": [ { "path": "相对路径", "kind": "pdf|md|txt|docx", "extracted": true } ],
  "uncertainty": [ { "locator": "材料定位", "issue": "冲突/缺口/待确认", "disposition": "处理" } ],
  "change_log": [ { "locator": "材料定位", "quote": "逐字原文", "before": "", "after": "", "reason": "理由", "kind": "keep|compress|delete|rewrite|add_explain" } ],
  "pending_human": [], "errors": [], "degradations": [],
  "runtime": { "started_at": "", "ended_at": "", "seconds": 0 }
}
```

硬规则：主稿必须是**可编辑产物**（`.md` 优先；契约要求 Office 时产出 `.docx/.pptx/.xlsx`，并尽量附 PDF/PNG 预览）；`status` 诚实，材料不足就 `insufficient_evidence`，绝不编造数字、引用、DOI 或结论；冲突信息两个都记并标 `source_conflict`。

## 4. 执行流程（总预算 900s / 64 次模型请求，每次 ≤16384 token）

| 步 | 时间 | 输入 | 动作 | 输出 |
|---|---|---|---|---|
| 0 骨架落盘 | 0–3 min | case.json, task.md, /input | 跑 `python3 -B scripts/main.py prepare --input /input --output /app/output --work /app/work`，立即写出合法 partial 骨架 + 材料索引 | result.json(partial), /app/work/materials_index.json, evidence_map.json 脚手架 |
| 1 读题与读者画像 | 3–6 min | task.md, case.json | 锁定目标读者、体裁、篇幅、术语密度；定取舍优先级 | 决策笔记（可写入 work） |
| 2 精读底稿取证据 | 6–10 min | 材料索引 → 材料原文 | 逐段抽取关键结论、数值+单位+比较基准、样本量、统计口径、图表编号、引用 | 证据草稿（facts 逐条带 locator+quote） |
| 3 成稿 | 10–13 min | 证据 + 约束 | 写出完整主稿，删冗余不删证据，缩写首展开，术语统一 | /app/output/manuscript.md（或契约指定格式） |
| 4 溯源与改动清单 | 13–14.5 min | 主稿 vs 源文 | 逐条回填 facts/uncertainty/change_log | 填好 /app/work/evidence_map.json |
| 5 收口自检 | 14.5–15 min | 全部产物 | 跑 `python3 -B scripts/main.py finalize ...` 再 `check ...`，按第 5 节清单核对 | 最终 result.json + 主稿 |

**硬约束**：第 0 步必须在 3 分钟内落盘 result.json 骨架；之后任何一步都只做增量改进，不推倒重来。模型请求若不够，优先保"主稿 + facts 溯源"，change_log 只保留有据可查的条目。请求用完或超时，立即跑 finalize 收口，把未完成项写进 `pending_human` 与 `status=partial`，不要空手结束。

## 5. 自检清单（对应评分维度，逐条可勾选）

- [ ] 主稿是可直接使用的完整稿件，非提纲/建议/片段（实际效果·完成度）
- [ ] 数值、单位、比较基准、样本量、口径、图表编号与引用逐一与源文一致，无源文未支持的新结论（实际效果·正确性）
- [ ] 关键结论、限定条件、方法要点、图表引用全部保留；删的是冗余不是证据（实际效果·完整性）
- [ ] 提供逐段对照 + 改动清单 + 删改理由 + 待确认问题，作者可按段复核（实际效果·可核验性）
- [ ] 缩写首次展开、同义词收敛、术语表全文唯一（使用增益·质量）
- [ ] result.json 含 case_id + 全部 contract 字段 + status/sources/uncertainty/change_log（可靠体验）
- [ ] 坏 JSON/坏编码/空目录/缺材料均降级不崩溃，退出码 0（可靠体验·异常）
- [ ] 同一输入重复运行产物一致；无预置答案、无编造分数（可靠体验·稳定）
- [ ] 文档齐全：SKILL.md、体裁模板、术语表、验收口径、失败处理、版本信息（生态贡献）

## 6. 降级策略

缺数据/缺软件时如实标注，禁止编造：

- 材料为空或无法解析 → `status=insufficient_evidence`，把缺口写进 `pending_human`，主稿留"材料不足，无法改写"并列出缺失项。
- PDF 提取失败（`pdftotext` 缺失/坏 PDF）→ 记入 `degradations`，改用平台 `pdf-text-tool` 提取；仍失败则原文照录并标"待人工核对"。
- 体裁与篇幅冲突、数据自相矛盾、引用缺失、材料涉密/未发表 → 记入 `uncertainty`，标注 `source_conflict` 或 `pending_human`，不在稿内擅自裁决。
- 写盘失败 → 降级到最小合法 result.json（仅 case_id + status + errors），不让进程崩溃。

## 7. 脚本与资源

- `scripts/main.py`：prepare（骨架+材料索引）、finalize（合并证据、定 status）、check（结构自检）。纯标准库。
- `scripts/evidence.py`：材料读取容错、数字/缩写/论断强度抽取（仅辅助候选，需人工核对，不自动写入稿件）。
- `references/rewrite-rules.md`：四项验收规则 + 论断强度分级 + 术语锁定 + 证据格式。
- `references/genre-templates.md`：科普稿/项目申报/技术报告/论文摘要的模板与"可简化 vs 必须保留"对照。

所有脚本用相对路径调用：`python3 -B scripts/main.py ...`。
