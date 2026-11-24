---
name: literature-tracking
description: 科研文献“最新发现与增量追踪”Skill。读取 /input/case.json 与 task.md，执行订阅配置→时间窗检索→快照比对→去重归一→新增清单→规则提醒的闭环，输出可回放、带 locator+quote 证据的 result.json 与 report.md。用于主题订阅、定期检索、增量识别、去重、版本更新与撤稿更正判定。
version: 1.1.0
---

# 文献发现与追踪 Skill

## 1. 角色与何时使用

本 Skill 交付一条**可按时执行、可独立复核、可重复运行**的文献追踪链路，而非一次性主题检索。核心：明确问题与范围 → 按时间窗检索 → 与上次快照比对 → 去重/版本归并 → 产出新增清单 → 按规则提醒，每一步保留筛选依据（locator + 逐字 quote）。

**适用**：主题订阅、定期检索、增量识别、去重归一、预印本→正式版→勘误版本链、撤稿更正、时间窗推进与回放。
**不适用**：全文精读、自动写综述、未授权定时抓取；提醒内容不是科学结论。

## 2. 输入契约

运行时固定输入：
- `/input/case.json`：可能含 `output_contract`、`case_id`、材料路径、时间窗与快照约束。
- `/input/task.md`：任务说明与评分要求。
- `/input/` 下 `materials/`、PDF、`notes.md` 等任意材料：**先扫描发现，不假设文件名/键名**。

约束：文本统一按 `utf-8-sig` 读取并容错；坏编码/坏 JSON 降级记 `errors` 不崩溃；单文件 2MB 上限，超限跳过记 `degradations`；PDF 用 `pdftotext` 或平台 PDF 工具读原文，不按文件名猜内容。缺省路径 `--input /input --output /app/output --work /app/work`。

## 3. 输出契约

最终产物写入 `/app/output/`：
- `result.json`（必需）：含 `case_id` + 题目 `output_contract` **全部字段原样回填**（缺失给 `null`/`[]`，不改名、不删未知键），再加：
  - `status`：`ok | partial | insufficient_evidence | input_error`
  - `sources`、`uncertainty`、`change_log`（首条为 `skeleton_written`）
  - `errors`、`degradations`、`pending_human`、`runtime`
- `report.md`：人类可读报告，与 result.json 一致。

无 `output_contract` 时领域字段至少覆盖：
```json
{
  "case_id": "...",
  "search_results": [],
  "ranking": [],
  "evidence": [],
  "status_notes": {"empty": null, "conflicts": [], "permission_errors": []},
  "tracking": {"subscription": null, "window": null, "snapshot": null,
               "new_items": [], "updates": [], "retractions": [],
               "corrections": [], "reminders": []},
  "dedup": {"priority": ["doi", "arxiv", "title"]}
}
```
`evidence` 每项必带 `locator` + `quote`；冲突两条都记并标 `source_conflict`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

| 阶段 | 输入 → 输出 | 预算 |
|-|-|-|
| 0 落骨架 | 读 case.json+task.md → `python3 scripts/main.py scaffold` → 写含全部契约字段的 result.json（status=partial） | ≤3 min，≤6 req |
| 1 问题与订阅 | 读全部材料（含 PDF 原文）→ 抽取研究问题、`subscription`（查询式/来源/频率） | ≤4 min，≤8 req |
| 2 时间窗与快照 | 定窗口起止（UTC）与上次 `snapshot.seen`（canonical_id 集合）→ 检索/解析本地材料得候选 | ≤3 min，≤8 req |
| 3 去重与增量 | `python3 scripts/main.py dedup` 做归一与版本链 → 与快照求差得 new/updated/removed，标撤稿/更正 | ≤3 min，≤12 req |
| 4 提醒与证据 | 合并抑制提醒（去重、去转载）→ 每条结论写 evidence（locator+quote） | ≤1 min，≤6 req |
| 5 收尾自检 | `python3 scripts/main.py finalize --findings /app/work/findings.json` → 写 result.json+report.md → `python3 scripts/selftest.py` | ≤1 min，≤6 req |

**前 3 分钟必须先落盘一版 partial 骨架**，之后只做增量改进；中途崩溃也留合法产物。保留 ≤18 次请求给限流重试、延迟入库补抓、跨窗边界与冲突复核。任何一步材料不足如实标 `insufficient_evidence`/`pending_human`，绝不编造文献、元数据或来源。

## 5. 领域核心规则

闭环：订阅配置 → 时间窗检索 → 快照比对 → 新增清单 → 规则提醒。精确规则见 `references/normalization.md`，运行前先读。要点：

1. 增量查询：快照水位代替全量重扫，`snapshot.seen` 存上一窗口 canonical_id 集合。
2. 去重：DOI/arXiv/归一标题三键 union-find；标题仅在“非双 DOI 且 DOI 不同”时作桥接，避免把同题异文误并。
3. 时间窗：闭区间连续无缝，UTC 归一，边界与延迟入库条目补回。
4. 提醒：只推真 new 与版本推进/撤稿更正；转载、旧文献、重复条目抑制。
5. 失败重试：限流/超时/改版可恢复且幂等，不二次推送。
6. 版本记录：配置、窗口、抓取时间、计数、去重键全量留痕，任意增量可复算。

证据溯源硬要求：每个关键结论/数值都指回材料逐字片段。来源边界：判定“新增/更新/撤稿”只依据实际输入文件与原文，不宣称实时联网覆盖或“全球最新”，超出材料范围标 `uncertainty`。

## 6. 自检清单（交付前逐条勾选）

- [ ] 实际效果：result.json 合法、含 case_id、output_contract 全字段回填；每条候选有 locator+quote；新增条目真实存在且属本窗口；预印本/正式版/撤稿关系正确；时间窗无缝。
- [ ] 使用增益：去重键稳定、重复与误报被抑制、相同快照+窗口+来源可复算，减少人工核对条数。
- [ ] 创新增益：形成增量状态/去重归一/版本链合并/提醒抑制能力，并说明边界与价值。
- [ ] 可靠体验：空输入、坏 JSON、缺参数、写盘失败、限流、日期缺失、时区错位、撤稿更正均有处置且幂等。
- [ ] 生态贡献：SKILL.md、scripts、references 齐全，脚本标准库+相对路径可直接复用。

## 7. 降级策略

- 材料缺失/无法判断 → `insufficient_evidence` + `pending_human` 列缺口，不补造。
- 软件/工具缺失 → 标准库最小实现降级；做不到写 `degradations`+`limitations`。
- 写盘失败 → 最小合法 JSON（case_id+status+errors），不崩溃。
- 限流/超时/改版 → 幂等重试；仍失败如实记录，不伪造响应、不二次推送。
- 字段缺失 → `null`/`not_reported`；冲突不隐藏，标 `source_conflict`。

## 8. 脚本调用（相对路径，标准库）

```bash
python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
python3 scripts/selftest.py
```

先 `python3 scripts/main.py --help` 确认接口。脚本只做确定性骨架、去重、合并与落盘；实质检索与判断由 Agent 完成并写入 `findings.json`。重跑必须同结果。

## 9. findings.json 接口（Agent ↔ 脚本）

Agent 完成实质判断后写入 `/app/work/findings.json`：
```json
{
  "subscription": {"query": "", "sources": [], "frequency": ""},
  "window": {"start": "YYYY-MM-DDTHH:MM:SSZ", "end": "...", "tz": "UTC"},
  "snapshot": {"seen": {}},
  "records": [{"doi": "", "arxiv": "", "title": "", "authors": [], "journal": "",
               "date": "", "version": "", "status": "", "source": "",
               "locator": "", "quote": ""}],
  "ranking": [], "evidence": [], "reminders": [],
  "uncertainty": [], "errors": [], "degradations": [], "pending_human": [],
  "status": "ok"
}
```
脚本据此做归一、去重、版本链、增量分类与落盘；不确定处由 Agent 在 evidence/uncertainty 中注明。
