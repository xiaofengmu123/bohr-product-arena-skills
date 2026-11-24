---
name: sci-viz-checkpoint
description: 科研数据可视化全流程 Skill：以“预算台账 + 阶段检查点”驱动，从 CSV/SQLite 原始数据到可投稿图件的清洗、统计、选图、编码、标注与发表级导出，先落盘可复核的 partial 结果骨架再增量改进，每一步记录证据溯源、图注与失败原因。当需要把科研数据做成可复跑、逐元素可核对的图件，且受 900 秒 / 64 次模型请求预算约束时使用。
version: 1.0.0
---

# 科研数据可视化 · 检查点流水线

## 1. 角色与触发时机

- 角色：你是“数据 → 可投稿图件”流水线的执行者，产出真实图件 + 绘图代码 + 图注 + 数据字典 + 证据映射 + 预算台账，而不是“建议画什么图”的文字。
- 触发：`/input` 出现 `case.json` + `task.md`，任务要求对科研数据做清洗、统计、可视化并交付可复核结果。
- 不适用：纯文字问答、无数据的画图建议、实验设计或统计方法创新、图像后期美化。

## 2. 输入契约

- 固定路径：`/input/case.json`（任务与 output_contract）、`/input/task.md`（题面）；材料可能在 `/input` 下的 `*.csv|*.tsv|*.txt|*.md|*.db|*.sqlite|*.sqlite3|*.xlsx|*.xls|*.parquet|materials/`。
- 工作目录 `/app/work`，最终产物（含 `result.json`）写到 `/app/output/`。
- 先发现再假设：不假设固定文件名/列名/键名；文本按 `utf-8-sig` 读，坏 JSON/坏编码逐级降级并记录；单文件 >2MB 跳过并记入 `degradations`。
- output_contract 从 `case.json`（键名可为 `output_contract/outputContract/result_schema/expected_output/contract` 等）或 `task.md` 的 JSON 代码块提取；找不到时用最小字段集兜底并在 `uncertainty` 记录 `pending_human`。

## 3. 输出契约（/app/output/result.json）

必须产出，字段如下：

- `case_id`：取 case.json 的 `case_id/id/challenge_id`；缺失时用输入目录名 + 内容哈希。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实赋值。
- **output_contract 全部字段原样回填**：不改名、不删未知键、不改层级；无法取值给 `null`（列表给 `[]`）。
- `sources`：证据映射数组，每项 `{locator, quote, kind}`；关键数值/图元逐字引用；冲突并存 `source_conflict`。
- `uncertainty`：数组，记录不确定项、原因与 `pending_human` 缺口。
- `change_log`：数组，每项 `{ts, seq, step, phase, action, files}`；首次落盘与每次增量改进必记。
- `budget`：预算台账（本 Skill 独有），记录总预算、阶段计划、各阶段 `done` 标记与 `elapsed_seconds`，见第 4 节。
- 附加：`errors`、`degradations`、`runtime`、`artifacts`（主产物路径与格式）。

保留键（`case_id/status/sources/uncertainty/change_log/errors/degradations/runtime/artifacts/budget`）由本 Skill 维护；若契约与保留键同名，以本 Skill 语义为准并在 `uncertainty` 注明。

## 4. 预算台账与早落盘（本版核心）

把 900 秒 / 64 次模型请求拆成阶段预算，**前 3 分钟必须先落盘一版结果骨架**，之后只做增量改进：

| 阶段 | 时间窗 | 请求预算 | 门槛（gate） |
|-|-|-|-|
| A 骨架落盘 | 0–3 分钟 | ≤4 | `result.json`（status=partial）+ `work/plan.md` 已写盘 |
| B 主分析 | 3–12 分钟 | ≤45 | 清洗/统计/选图/编码/标注/导出逐项落盘 |
| C 收尾自检 | 12–15 分钟 | ≤10 | `check` 通过，`status` 与 `runtime.finished_at` 已更新 |
| 保留 | — | 5 | 仅用于失败恢复 |

规则：

- 阶段 A 无论数据是否齐全都必须完成：先写 `result.json` 骨架，任何后续崩溃都有合法产物。
- 每次 `commit` 用 `--phase A|B|C` 标记所属阶段，脚本自动更新 `budget.phases[].done` 与 `budget.elapsed_seconds`；用 `python3 scripts/main.py ledger` 查看剩余预算。
- 每步一次请求做一件事，先读后写，中间产物落 `work/` 下。
- 预算将尽（阶段 B 用满 45 次或临近 900 秒）时立即停止扩展，降级到可用子集并记录 `degradations`，绝不编造。

## 5. 执行流程

阶段 A（0–3 分钟，必须完成）：

1. 读 `/input/case.json`、`/input/task.md`，扫描材料文件。
2. 在 Skill 根目录运行：`python3 scripts/main.py init --input /input --output /app/output --work /app/work`
3. 核对生成的 `result.json` 骨架与 `work/plan.md`：契约字段是否齐全、`status` 是否正确。

阶段 B（3–12 分钟，主分析）：按顺序执行，每步结束后运行 `commit` 增量落盘：

1. 读数据字典与字段语义，记录清洗口径（缺失/异常/重复/离群）。
2. 清洗：先记录再处理，不静默删除或填充；剔除保留原始值与规则。
3. 统计：描述统计、分组、相关/趋势；写清方法（SD/SEM/CI 与 n）。
4. 选图：按数据类型与分析目的选图（详见 `references/figure-spec.md`）。
5. 编码/分面/配色；标注与图注；显著性标记与误差线定义。
6. 导出 SVG + PDF + ≥300 dpi PNG，尺寸匹配栏宽，中文无乱码。
7. 每步示例：`python3 scripts/main.py commit --phase B --step clean --log "缺失按 pairwise 处理，剔除 3 条离群并保留原值" --files "work/clean.csv,work/clean_log.md"`

阶段 C（12–15 分钟，收尾）：

1. `python3 scripts/main.py check --output /app/output --require-contract` 自检。
2. 补齐图注/证据/uncertainty，设 `status`，更新 `runtime.finished_at/seconds`。
3. 再次 `check`，确认退出码 0（除非 `--fail` 命中 FAIL）。

## 6. 数据与图表规则（逐条落实）

- 清洗：缺失/异常/重复先记录再处理，写清口径，不静默删除或填充。
- 选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/小提琴/点图；构成→堆叠/归一化条形；相关趋势→散点/折线；多变量→散点矩阵/相关热图/分面。不把无序分类画成饼图、不把时间序列画成并列柱。
- 编码：优先位置/长度；面积与角度会夸大差异；类别 >6 聚合或分面；量纲差异大分面或标准化；对数轴显式标注。
- 标注：轴标题=变量名(单位)；图例对应数据字典；误差线写明 SD/SEM/CI 并给 n；显著性给 p 值或记号定义；图注脱离正文可读。
- 可读性：Okabe-Ito 色盲安全配色，灰度可辨；字号 ≥7pt；单栏宽 3.4 in。
- 导出：SVG + PDF（字体嵌入）+ ≥300 dpi PNG；中文先探测 CJK 字体，缺字体改英文标签并记录 degradation。

## 7. 自检清单（对应评分维度）

- [ ] `result.json` 合法且含 `case_id` + 全部契约字段 + `status/sources/uncertainty/change_log/budget`（可核验性）
- [ ] 每个关键数值/图元有 `locator` + 逐字 `quote`；冲突并存 `source_conflict`（可核验性）
- [ ] 图上数值/轴类型与刻度/单位/图例/误差线含义/显著性/色标范围与源数据一致（正确性）
- [ ] 覆盖任务全部图表要素，交付绘图代码与图注（完成度/完整性）
- [ ] 导出 SVG+PDF+≥300dpi PNG，尺寸匹配栏宽，中文无乱码（发表级导出）
- [ ] 重跑两次统计与图形一致（固定随机种子/排序/布局）（复现性）
- [ ] 缺失/异常/失败均显式说明，无编造（异常处理）
- [ ] 空输入/坏 JSON/缺参数也产出合法 `result.json` 且退出码 0（健壮性）
- [ ] `budget.phases` 各阶段已标记，阶段 A 在 3 分钟内完成（预算纪律）

## 8. 降级策略

- 缺数据：`status=insufficient_evidence`，列出缺口，不编造数值/结论。
- 缺软件（无 matplotlib/CJK 字体）：用可用子集完成（纯文本统计 + 数据表），`degradations` 标注 limitations，不伪装成完整图。
- 缺 output_contract：最小字段集兜底，`uncertainty` 标 `pending_human`。
- 写盘失败：降级到最小合法 `result.json`，进程不崩溃（脚本异常也退出码 0）。
- 预算将尽：停止扩展、保留已落盘 partial 结果并如实标注未完成项。

## 9. 脚本调用

```bash
cd /skills/weekly-skill
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/main.py commit --phase B --step clean --log "..." --files "work/x.csv"
python3 scripts/main.py commit --set 'status="ok"' --log "完成全部图件与图注"
python3 scripts/main.py ledger --output /app/output
python3 scripts/main.py check --output /app/output --require-contract
python3 scripts/selftest.py
```

示意输出（标注为示例）：`{"case_id":"demo-000000000000","status":"ok","sources":[{"locator":"data.csv#L2","quote":"...","kind":"value"}],"budget":{"phases":[{"id":"A","done":true}, ...]}}`

## 10. 边界

- 一个 Skill 只解决“科研数据 → 可投稿图件”一类任务，不做通用大而全的绘图框架。
- 没有真实材料时输出“材料不足”而不是示例填充。
- 不编造绘图结果、字体状态、统计结论或 benchmark 分数；一切以 `/input` 实际材料为准。
