---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill：把论文/研究材料转成可讲、可核验、可编辑的学术 PPT。先出页级计划（一页一主张 + 图表/章节定位），再按模板成稿并导出渲染预览。强调证据溯源（locator + 逐字 quote）与降级/不确定性显式化（缺数据不编造）。
version: 1.0.0
---

# 论文转学术 PPT（slides-01）· 900 秒 / 64 请求内的最短可靠路径

## 1. 角色与何时使用

本 Skill 用于把论文或研究材料转成**可讲的学术 PPT**：不是把段落切成行，而是结构重写——提取研究问题、方法、结果、限制形成主线；设计页级主张与图表引用；按给定模板生成可编辑演示文件与讲稿备注；为每页保留论文定位并检查导出渲染。当任务是“论文 → 学术汇报 PPT（slides-01）”、需要产出可打开、可放映、可逐页编辑且证据可回溯的演示时使用。不负责论文精读结论生成、科研绘图美化、学术润色。

判断做对的三件事：主线与论文贡献一致（非段落顺序搬运）；每页可回溯到图/表/章节/数据；成品在指定模板与软件中可打开、播放、编辑、复跑出同一版式。

## 2. 输入契约

- 权威契约：`/input/case.json`（含 `case_id` 与 `output_contract`，字段名以其为准）。
- 任务说明：`/input/task.md`；材料：论文 PDF、汇报稿、模板 pptx、图表、听众/时长要求等。
- **先发现后假设**：用 `python3 -B scripts/main.py --cmd plan` 扫描材料，不假定固定文件名；`case.json` 之外的文件按后缀归类（pdf/template/figure/text/office）。
- 文本用 `utf-8-sig` 读取并容错；单文件 >2MB 跳过并记录。PDF 若加密或无文本层，如实记录 `limitations`。

## 3. 输出契约（必须落盘到 `/app/output/`）

1. `result.json`（必须，否则 0 分）：含 `case_id`、`status`、题目 `output_contract` 的**全部字段**（缺值给 `null`/`[]`/`{}`，不改名不删键），再加统一信封：`sources` / `uncertainty` / `pending_human` / `limitations` / `errors` / `degradations` / `change_log` / `runtime`。
2. 真实可编辑 `presentation.pptx` + 页数一致的 `presentation.pdf`（用真实导出 PDF 预览；无 pptx 能力时给 SVG+PNG 并写 `degradations`/`exceptions`）。
3. `work/plan.json` 或 `work/draft.json`（页级计划，供复核）。
4. `report.md`（方法、证据映射、修改记录、限制与不确定性说明）。

`status` 四档，如实使用：`ok` / `partial` / `insufficient_evidence` / `input_error`。产物按契约字段 `editable_source` / `export_artifact` 指向真实文件，不写占位空文件。

## 4. 执行流程（分步，各步输入/输出，时间纪律）

总预算 **900 秒 / 64 次模型请求 / 单响应 16384 tokens**。**前 3 分钟必须先落盘一版 partial 结果骨架，之后只做增量改进**；每 40 秒至少写一次文件；连续思考不超 2 分钟；剩 5 分钟必须已落盘可交接版本。

| 步 | 动作（输入 → 输出） | 预算 |
|---|---|---|
| 0 | 看 `/input/case.json` 记 `case_id` + `output_contract` 字段名；`python3 -B scripts/main.py --cmd skeleton` → 立刻写 `result.json` 骨架 | ≤1 分钟 |
| 1 | `python3 -B scripts/main.py --cmd plan` → 写 `work/draft.json` 骨架；读 `task.md` + 材料，抽主线（问题/方法/结果/限制） | 1–4 分钟 |
| 2 | 逐页填 `draft.json`：每页 `{slide, title, claim, role, evidence[], notes, evidence_status}`，证据带 `locator` + 逐字 `quote` | 4–9 分钟 |
| 3 | 按模板生成 pptx（继承母版/版式，禁止“新建空白+贴文本框”）；导出 PDF 预览 | 8–12 分钟 |
| 4 | `python3 -B scripts/main.py --cmd finalize`（合并 draft→result）；`--cmd pdfcheck`（页数一致）；`--cmd audit`（证据缺口） | 12–14 分钟 |
| 5 | 补 `report.md`；按 §5 逐项自检；把 `draft.json` 增量改进后再 `finalize` | 14–15 分钟 |

**证据硬规则**：每个关键结论/数值/图元给 `source_id + locator + quote`（quote 逐字命中材料，见 §7 定位写法）。读不出 → 写 `uncertainty`/`pending_human`/`limitations`，**绝不编造**数字、单位、DOI、引用。

## 5. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] `result.json` 含 `case_id` + `output_contract` 全部字段 + `status/sources/uncertainty/change_log`
- [ ] `status` 诚实：材料不足为 `insufficient_evidence`，未编造数据/结论
- [ ] 每页有 `claim/role/evidence/notes`，证据有 `locator` + 逐字 `quote`（`evidence_status` 标 `verified/inferred/not_reported/conflicting`）
- [ ] 图表编号与正文引用一致，单位/数值/方法名未改写，示意图未当实验数据
- [ ] 主线与论文贡献一致（非段落顺序搬运），主结论与支撑细节分层
- [ ] pptx 真继承模板母版/版式；长标题未溢出、字号可读；讲稿备注与页面文字不重复
- [ ] PDF 页数与 pptx 一致（`pdfcheck` 通过）；换机/导出不崩版
- [ ] `report.md` 记录方法、证据映射、字体/模板、修改记录（`change_log`）
- [ ] 空输入/坏 JSON/缺输入三种情况 `python3 -B scripts/selftest.py` 全通过
- [ ] 未发明评测分数、运行结果或引用；示意输出已标“示例”

## 6. 降级与不确定性（本版重点：显式化，缺数据不编造）

遇到下列情况**必须**如实标注，不得用猜测补空：

- 图表无法提取 / 公式为图片 / PDF 加密或无文本层 → `limitations` 记“图 X 未能提取，未复述其数值”；关键页写 `pending_human`。
- 模板占位符不足、字体缺失、页数超限 → `limitations` + `degradations`，并在 `report.md` 说明替代版式。
- 数值/单位在材料中不一致 → 两处都记，标 `source_conflict`（证据 `status=conflicting`），不擅自选一个。
- 无 LibreOffice/python-pptx 导出 PDF → 给 SVG+PNG，`exceptions` 说明，`export_artifact` 指向真实存在的预览件。
- 材料未报告的指标 → `not_reported` + `uncertainty`（含 `about/reason/mitigation`），不填数字。

每条 `uncertainty` 用 `{about, reason, evidence_status, mitigation}`；每条 `limitations` 说明“缺什么、影响哪页、如何补救”。**缺数据不是失败，编造才是失败。**

## 7. 证据定位写法（locator 规范）

- 材料文件用相对路径 + 页码/段号：`materials/paper.pdf#p3`、`materials/paper.md#p4¶2`、`materials/fig1.png`。
- `quote` 必须逐字命中材料原文（可从 PDF 文本层复制的原句）；转写/概括标 `inferred`，不写 `verified`。
- 页级证据映射写入 `content_map[].evidence[]` 与 `work/draft.json` 的 `evidence_index`，供他人复核与复跑。

## 8. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py --cmd skeleton   --input /input --work /app/work --output /app/output
python3 -B scripts/main.py --cmd plan       --input /input --work /app/work --output /app/output
python3 -B scripts/main.py --cmd finalize   --input /input --work /app/work --output /app/output
python3 -B scripts/main.py --cmd pdfcheck   --output /app/output
python3 -B scripts/main.py --cmd audit      --work /app/work --output /app/output
python3 -B scripts/selftest.py              # 提交前自检
```

- `skeleton` 写合法 partial 骨架；`plan` 写 `draft.json` 骨架（内容由你填，脚本不代写）；`finalize` 合并 draft→result；`pdfcheck` 校验 pptx/PDF 页数；`audit` 报证据缺口。
- 内容组织（主线、每页主张、证据、取舍）由**你本人**真实阅读材料后写入 `work/draft.json`；读不出写缺口，禁止占位空文件。
- pptx 生成用运行环境提供的 `python-pptx`（打开模板、编辑占位符）或 LibreOffice 导出 PDF；本 Skill 脚本不内置二进制生成，避免伪继承。

## 9. 边界与失败处理

- `/input` 缺失或坏 JSON：仍写 `result.json`，`status="input_error"`、`case_id="unknown-case"`，`errors` 记原因。**没有 result.json 才是 0 分。**
- 两策略失败后停止重复；所需软件/数据缺失则停止依赖步骤并报告，不伪造软件状态。
- 重复运行结果一致：固定证据状态判定规则、确定性布局；`runtime` 记录起止时间与版本，不记录虚构基准分数。
