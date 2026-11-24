---
name: literature-tracking
description: 科研文献“最新发现与增量追踪”Skill。读取 /input/case.json 与 task.md，跑通“订阅配置—按时间窗检索—与上次快照比对—去重归一—产出新增清单—按规则提醒”闭环，输出可回放、带 locator+逐字 quote 的 result.json 与 report.md。本变体以“降级与不确定性显式化”为核心：每条证据带置信度，每个缺口落 typed limitations/uncertainty，缺数据绝不编造。适用于主题订阅、定期检索、增量识别、去重、版本更新与撤稿更正判定。
version: 2.0.0
---

# 文献发现与追踪 Skill（降级与不确定性显式化变体）

## 1. 角色与何时使用

本 Skill 交付一条**可按时执行、可独立复核、可重复运行**的文献追踪链路，而不是一次主题检索。核心任务：明确研究问题与检索范围 → 按时间窗检索 → 与上次快照比对 → 去重/版本归并 → 产出新增清单 → 按规则提醒，每一步保留筛选依据（locator + 逐字 quote）。

**适用**：持续跟踪领域新论文、预印本→正式版→勘误的版本变化、增量更新提醒、去重归一、时间窗推进与回放。

**不适用**：全文精读、自动写综述、未经授权的定时抓取、把提醒当作科学结论；一次性脚本或纯问答不必打包成本 Skill。

**本变体设计取舍**：与基线相比，本包把「诚实」做成可机械审计的受控词表——每条证据标 `confidence`（verbatim/paraphrased/inferred/unreported/conflict），每个不确定性标 `severity`/`resolution`/`affects`，每个客观边界落 `limitations`（reason 码）。缺数据时写 `not_reported`/`unreported` 并记缺口，绝不补造文献、元数据或来源。

## 2. 输入契约

运行时固定输入：

- `/input/case.json`：案例输入，可能含 `output_contract`、`case_id`、材料路径、时间窗与快照约束。
- `/input/task.md`：任务说明与评分要求。
- `/input/` 下 `materials/`、PDF、`notes.md` 等**任意材料**：先扫描发现，不假设固定文件名/键名。

约束：

- 文本按 `utf-8-sig` 读取并容错；坏编码、坏 JSON 降级并记 `errors`/`limitations(reason=encoding/parse_failed)`，不崩溃。
- 单文件 2MB 上限，超限跳过并记 `degradations(reason=size_cap)`。
- **先发现，再假设**：实际文件名、字段名、内容以磁盘为准；PDF 用 `pdftotext` 或平台 PDF 工具读原文，不得按文件名猜内容。
- 缺省路径：`--input /input`、`--output /app/output`、`--work /app/work`。

## 3. 输出契约

最终产物写入 `/app/output/`：

- `/app/output/result.json`（必需）：`case_id` + 题目 `output_contract` 的**全部字段原样回填**（缺失给 `null`/`[]`，不改名、不删未知键），再加：
  - `status`：`ok | partial | insufficient_evidence | input_error`
  - `sources`：来源与定位列表
  - `uncertainty`：结构化不确定性（`severity`、`resolution`、`affects`、`locator`、`quote`）
  - `limitations`：结构化限制台账（`reason` 码、`scope`、`impact`）
  - `change_log`：每步事件与时间，含最早的 `skeleton_written`
  - `errors`、`degradations`、`pending_human`、`runtime`
- `/app/output/report.md`：人类可读报告（与 result.json 一致）。
- 若题目要求图表/Office 产物，另给可编辑文件 + 真实 PDF/PNG 预览。

`result.json` 领域字段（无 output_contract 时至少覆盖三行输出要求：检索结果/证据记录/状态说明）：

```json
{
  "case_id": "...",
  "search_results": [], "ranking": [], "evidence": [],
  "status_notes": {"empty": null, "conflicts": [], "permission_errors": []},
  "tracking": {"subscription": null, "window": null, "snapshot": null,
               "new_items": [], "updates": [], "retractions": [], "reminders": []},
  "dedup": {}, "limitations": [], "uncertainty": []
}
```

证据条目：`{"id","claim","confidence","locator","quote","basis"}`。逐字证据 `verbatim` 必须带非空 quote；空 quote 自动降级为 `paraphrased`，不得冒充逐字。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**前 3 分钟内必须先落盘一版 `partial` 骨架**，之后只做增量改进，中途崩溃也留合法产物。

| 阶段 | 步骤（输入 → 输出） | 预算 |
|-|-|-|
| 0 落盘骨架 | 读 `/input/case.json`+`task.md` → `python3 scripts/main.py scaffold` → 写含全部契约字段的 `result.json`（status=partial）。 | ≤3 min，≤6 req |
| 1 明确问题与订阅 | 读全部材料（含 PDF 原文）→ 抽研究问题、检索范围、`subscription`（查询式、来源、更新频率）。 | ≤4 min，≤8 req |
| 2 时间窗与快照 | 确定窗口起止（UTC）与上次 `snapshot`（canonical_id 集合）→ 检索/解析本地材料收集候选。 | ≤3 min，≤8 req |
| 3 去重与增量 | `python3 scripts/main.py dedup` 做归一与版本链 → 与快照求差得 `new/updated/removed`，标撤稿/更正。 | ≤3 min，≤12 req |
| 4 提醒与证据 | 合并抑制提醒（去重、去转载）→ 每条结论写 `evidence`（locator + quote + confidence）。 | ≤1 min，≤6 req |
| 5 收尾自检 | `python3 scripts/main.py finalize --findings /app/work/findings.json` → 写 result.json + report.md → 自检。 | ≤1 min，≤6 req |

保留 ≤18 次请求给限流重试、延迟入库补抓、跨窗边界与冲突复核。**任何一步材料不足都标 `insufficient_evidence` + `pending_human`/`limitations`，绝不编造。**

## 5. 领域核心规则

闭环：`订阅配置 → 时间窗检索 → 快照比对 → 新增清单 → 规则提醒`。精确规则见 `references/normalization.md`，运行前先读。要点：

1. 增量查询：快照水位代替全量重扫；`snapshot` 存上一窗口 canonical_id 集合。
2. 去重：DOI > arXiv > 归一标题 稳定键；处理大小写/标点、DOI 与 arXiv 并存、同文多源。
3. 时间窗：闭区间连续无缝、UTC 归一，边界与延迟入库条目补回。
4. 提醒：只推真 `new` 与版本推进/撤稿更正；转载、旧文献、重复条目抑制。
5. 失败重试：限流/超时/改版可恢复且幂等，不二次推送。
6. 版本记录：配置、窗口、抓取时间、计数、去重键全量留痕，任意增量可复算。

证据溯源是硬要求：关键结论/数值/图元都指回材料逐字片段；冲突两个都记并标 `source_conflict`。

**来源边界（本题事实依据）**：案例提供的是组织方保留的 Crossref/EuropePMC/出版社响应、引用图与增量导入状态；判定「新增/更新/撤稿」只依据实际输入文件与原文，不依据领域标签臆测，不宣称实时联网覆盖或「全球最新」，不伪造全文/引文/状态。任何超出材料可见范围的结论标 `uncertainty`；材料未给出的字段写 `not_reported`。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 实际效果：result.json 合法、含 case_id、output_contract 全字段回填；每条候选有来源定位+quote+confidence；新增条目真实且属本窗口；预印本/正式版/撤稿关系正确；时间窗无缝。
- [ ] 使用增益：去重键稳定、重复与误报被抑制、同快照+同窗口+同来源可复算（`fingerprint`），减少人工核对条数。
- [ ] 创新增益：形成基础方案没有的增量状态/去重归一/版本链/提醒抑制，且 confidence+uncertainty+limitations 显式化，并说明边界与价值。
- [ ] 可靠体验：空输入、坏 JSON、缺参数、写盘失败、限流、日期缺失、时区错位、撤稿更正均有处置且幂等；`scripts/main.py selftest` 通过。
- [ ] 生态贡献：SKILL.md、references 齐全，脚本标准库+相对路径可复用。

完整清单与降级细则见 `references/checklist.md`，交付前逐条核对。

## 7. 降级策略（缺数据不编造）

- 材料缺失/无法判断 → `insufficient_evidence` + `pending_human` + `limitations(reason=no_material)`，不补造。
- 软件/工具缺失 → 标准库最小实现降级；做不到写 `degradations` + `limitations(reason=tool_missing)`。
- 写盘失败 → 最小合法 JSON（case_id + status + errors），不崩溃。
- 限流/超时/改版 → 幂等重试；仍失败如实记录，不伪造响应、不二次推送。
- 字段缺失 → `null`/`not_reported`；冲突不隐藏，全部记录并标 `source_conflict`。
- 每个未判定点进入 `uncertainty`（severity/resolution），每个客观边界进入 `limitations`（reason 码）。

## 8. 脚本调用（相对路径，标准库）

```bash
python3 scripts/main.py --help
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py dedup --records /app/work/records.json --out /app/work/dedup.json
python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 scripts/main.py selftest --work /tmp/skill-selftest
```

脚本只做确定性骨架、去重、合并与落盘；实质检索与判断由 Agent 完成并写进 `findings.json`。重跑得到相同结果（`change_log` 时间戳除外）。
