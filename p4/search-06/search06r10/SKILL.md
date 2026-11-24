---
name: literature-delta
description: 科研文献“增量追踪”Skill。读取 /input/case.json 与 task.md，跑通“订阅配置—按时间窗检索—与上次快照比对—去重归一—产出新增清单—按规则提醒”的闭环，输出可回放、带证据定位(locator+逐字 quote)的 result.json 与 report.md。适用于主题订阅、定期检索、增量识别、去重、版本更新与撤稿更正判定；当任务要求“最新文献发现/文献检索/持续跟踪/追踪链路”时使用。
version: 1.0.0
---

# 文献增量追踪 Skill

## 1. 角色与何时使用

本 Skill 交付一条**可按时执行、可独立复核、可重复运行**的文献追踪链路，而不是一次主题检索。核心闭环：明确研究问题与检索范围 → 按时间窗检索 → 与上一次快照比对 → 去重/版本归并 → 产出新增清单 → 按规则提醒，并保留每一步的筛选依据（locator + 逐字 quote）。

**适用**：持续跟踪领域新论文、预印本→正式版→勘误的版本变化、增量更新提醒、去重归一、时间窗推进与回放。

**不适用**：全文精读、自动写综述、未经授权的定时抓取、把提醒内容当作科学结论。一次性脚本或纯问答不必打包成本 Skill。

## 2. 输入契约

运行时固定输入：

- `/input/case.json`：案例输入，可能含 `output_contract`、`case_id`、材料路径、时间窗与快照约束。
- `/input/task.md`：任务说明与评分要求。
- `/input/` 下 `materials/`、PDF、`notes.md` 等**任意材料**：先扫描发现，不假设固定文件名/键名。

约束：

- 所有文本按 `utf-8-sig` 优先读取并容错；坏编码、坏 JSON 降级并记 `errors`，不崩溃。
- 单文件 2MB 上限，超限跳过并记 `degradations`。
- **先发现，再假设**：实际文件名、字段名、材料内容以磁盘为准；PDF 用 `pdftotext` 或平台 PDF 工具读原文，不按文件名猜内容。
- 缺省路径：`--input /input`、`--output /app/output`、`--work /app/work`。

## 3. 输出契约

最终产物写入 `/app/output/`，必须含：

- `/app/output/result.json`（必需）：含 `case_id` + 题目 `output_contract` 的**全部字段原样回填**（缺失给 `null`/`[]`，不改名、不删未知键），再加：
  - `status`：`ok | partial | insufficient_evidence | input_error`
  - `sources`：来源与定位列表
  - `uncertainty`：不确定性/冲突列表
  - `change_log`：每步事件与时间，含最早的 `skeleton_written`
  - 附加：`errors`、`degradations`、`pending_human`、`runtime`
  - 领域字段：`search_results`、`ranking`、`evidence`、`status_notes`、`tracking`、`dedup`
- `/app/output/report.md`：人类可读报告（与 result.json 一致）。
- 若题目要求图表/Office 产物，另给可编辑文件 + 真实 PDF/PNG 预览。

`result.json` 的领域字段（无 `output_contract` 时覆盖题目三行输出要求）：

```json
{
  "case_id": "...",
  "search_results": [],
  "ranking": [],
  "evidence": [],
  "status_notes": {"empty": null, "conflicts": [], "permission_errors": []},
  "tracking": {"subscription": null, "window": null, "snapshot": null,
               "new_items": [], "updates": [], "retractions": [], "reminders": []},
  "dedup": {}
}
```

Agent 在阶段 1–4 的判定结论写入 `/app/work/findings.json`，供 `finalize` 合并并做去重/增量计算。字段如下（`records`、`evidence` 为必需，其余可缺省）：

```json
{
  "status": "ok|partial|insufficient_evidence|input_error",
  "subscription": {"query": "...", "sources": ["..."], "frequency": "..."},
  "window": {"start": "2026-01-01T00:00:00Z", "end": "2026-02-01T00:00:00Z", "tz": "UTC"},
  "snapshot": {"previous_ids": ["doi:10.x/...", "arxiv:2501.00001"],
               "captured_at": "2026-01-01T00:00:00Z"},
  "records": [{"title": "...", "doi": "...", "arxiv": "...", "authors": [],
               "journal": "...", "published_date": "...", "version": "preprint|journal|correction|retraction",
               "source": {"file": "...", "locator": "...", "quote": "..."}}],
  "search_results": [],
  "ranking": [],
  "evidence": [{"locator": "...", "quote": "...", "claim": "..."}],
  "uncertainty": [],
  "pending_human": [],
  "errors": [],
  "degradations": []
}
```

脚本不负责“发现新文献”这一判断本身；它只做确定性的归一、去重、求差与落盘。实质检索与相关性判断由 Agent 完成，结论写入 `findings.json`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

每一步输入→输出与时间分配如下。**前 3 分钟内必须先落盘一版 `partial` 结果骨架**，之后只做增量改进，中途崩溃也留合法产物。

| 阶段 | 步骤（输入 → 输出） | 预算 |
|-|-|-|
| 0 落盘骨架 | 读 `/input/case.json`+`task.md` → `python3 scripts/main.py scaffold` → 写入含全部契约字段的 `result.json` 骨架（status=partial）。 | ≤3 min，≤6 req |
| 1 明确问题与订阅 | 读全部材料（含 PDF 原文）→ 抽取研究问题、检索范围、`subscription`（查询式、来源、更新频率）。 | ≤4 min，≤8 req |
| 2 时间窗与快照 | 从材料确定窗口起止（UTC）与上次 `snapshot`（已见 canonical_id 集合）→ 执行检索/解析本地材料，收集候选。 | ≤3 min，≤8 req |
| 3 去重与增量 | `python3 scripts/main.py dedup` 做 DOI/arXiv/标题归一与版本链 → 与快照求差得 `new/updated/removed`，标记撤稿/更正。 | ≤3 min，≤12 req |
| 4 提醒与证据 | 合并抑制提醒（去重、去转载）→ 为每条结论写 `evidence`（locator + 逐字 quote）。 | ≤1 min，≤6 req |
| 5 收尾自检 | `python3 scripts/main.py finalize --findings /app/work/findings.json` → 写 result.json + report.md → 逐条过自检清单。 | ≤1 min，≤6 req |

保留 ≤18 次请求给限流重试、延迟入库补抓、跨窗边界与冲突复核。**任何一步材料不足都如实标 `insufficient_evidence`/`pending_human`，绝不编造文献、元数据或来源。**

## 5. 领域核心规则

闭环必须完整：`订阅配置 → 时间窗检索 → 快照比对 → 新增清单 → 规则提醒`。去重键、版本链、时间窗、增量判定、提醒抑制、失败重试的精确规则见 `references/normalization.md`，运行前先读它。要点：

1. 增量查询：用快照水位代替全量重扫；`snapshot` 存上一窗口 canonical_id 集合。
2. 去重：DOI > arXiv > 归一标题 的稳定键；处理大小写/标点、DOI 与 arXiv 并存、同文多源。
3. 时间窗：闭区间连续无缝，UTC 归一，边界与延迟入库条目补回。
4. 提醒：只推真 `new` 与版本推进/撤稿更正；转载、旧文献、重复条目抑制。
5. 失败重试：限流/超时/改版可恢复且幂等，不二次推送。
6. 版本记录：配置、窗口、抓取时间、计数、去重键全量留痕，任意增量可复算。

证据溯源是硬要求：每个关键结论/数值/图元都要能指回材料的**逐字片段**；冲突信息两个都记并标 `source_conflict`。

**来源边界（本题事实依据）**：案例提供的是组织方保留的 Crossref/EuropePMC/出版社响应、引用图与增量导入状态；判定“新增/更新/撤稿”只能依据这些实际输入文件与原文，不依据领域标签臆测，不宣称实时联网覆盖或“全球最新”，不伪造全文/引文/状态。任何超出材料可见范围的结论都要标 `uncertainty`。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 实际效果：result.json 合法、含 case_id、output_contract 全字段回填；每条候选有来源定位+quote；新增条目真实存在且属本窗口；预印本/正式版/撤稿关系正确；时间窗无缝。
- [ ] 使用增益：去重键稳定、重复与误报被抑制、相同快照+窗口+来源可复算，减少人工核对条数。
- [ ] 创新增益：形成基础方案没有的增量状态/去重归一/版本链合并/提醒抑制能力，并说明其边界与价值。
- [ ] 可靠体验：空输入、坏 JSON、缺参数、写盘失败、限流、日期缺失、时区错位、撤稿更正均有处置且幂等。
- [ ] 生态贡献：SKILL.md、references 齐全，脚本标准库+相对路径可直接复用。

完整清单与降级细则见 `references/checklist.md`，交付前逐条核对。

## 7. 降级策略

- 材料缺失/无法判断 → `insufficient_evidence` + `pending_human` 缺口，不补造。
- 软件/工具缺失 → 标准库最小实现降级；做不到的写 `degradations` + `limitations`。
- 写盘失败 → 最小合法 JSON（case_id + status + errors），不崩溃。
- 限流/超时/改版 → 幂等重试；仍失败如实记录，不伪造响应、不二次推送。
- 字段缺失 → `null`/`not_reported`；冲突不隐藏，全部记录并标 `source_conflict`。

## 8. 架构差异（本版设计取舍）

与“单入口 main.py + 内联 pipeline”的常见实现不同，本包显式分层：

- **模块拆分**：`main.py` 只做参数解析与路由（<30KB），全部领域逻辑在 `core.py`。
- **中间表示**：统一 canonical record（`canonical_id` + `version_chain` + `members`），脚本与 Agent 通过 `findings.json` 交换。
- **校验顺序**：`PIPELINE` 是显式阶段注册表，每阶段 `run` 后立即 `validate`，事件写入 `change_log`，顺序可审计。
- **原子写 + 幂等**：临时文件 + `os.replace`；领域字段在相同输入下逐字节一致。

## 9. 脚本调用（相对路径，标准库）

```bash
python3 scripts/main.py --help
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py dedup --records /app/work/records.json --out /app/work/dedup.json
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
python3 scripts/main.py run --input /input --output /app/output --work /app/work   # 确定性兜底
```

先 `python3 scripts/main.py --help` 确认接口；脚本只做确定性的骨架、去重、合并与落盘，实质检索与判断由 Agent 完成并把结论写进 `findings.json`。重跑必须得到相同的领域结果（除 runtime/change_log 时间戳）。交付前跑 `python3 scripts/selftest.py`（14/14 通过）。
