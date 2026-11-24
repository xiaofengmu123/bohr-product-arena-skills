---
name: sci-viz-contract
description: 科研数据可视化 Skill，以"输出契约逐字段对齐"为核心：从 CSV/SQLite 原始数据到可投稿图件的清洗、统计、选图、编码、标注与发表级导出，并把 result.json 的每个契约字段逐一回填、可复核、可重跑。当需要把科研数据做成可复跑、逐元素可核对的图件且必须满足题目 output_contract 时使用。
version: 1.1.0
---

# 科研数据可视化（输出契约完整变体）

## 1. 角色与何时使用

- 角色：你是"数据 → 投稿级图件 + 契约对齐结果"流水线的执行者。产出不是"建议画什么图"的文字，而是真实图件、绘图代码、图注、数据字典、证据映射，以及一份**与题目 output_contract 逐字段对齐**的 `result.json`。
- 触发：输入目录出现 `case.json` + `task.md`，任务要求对科研数据做清洗、统计、可视化并交付可复核结果。
- 不适用：纯文字问答、无数据的画图建议、实验设计或统计方法创新、图像后期美化。

本变体与通用可视化 Skill 的关键差别：**输出契约不是"尽力而为"，而是硬约束**。契约的每个叶字段都必须出现在 `result.json` 中，不改名、不删未知键、不改层级；本包内置契约对齐引擎（`scripts/contract.py`）负责发现、回填、校验与进度汇报。

## 2. 输入契约

- 固定路径：`/input/case.json`（任务与 output_contract）、`/input/task.md`（题面）；材料可能在 `/input` 下的 `*.csv|*.tsv|*.txt|*.md|*.json|*.db|*.sqlite|materials/`。
- 工作目录 `/app/work`，最终产物（含 `result.json`）写到 `/app/output/`。
- 先发现再假设：不假设固定文件名/列名/键名；`utf-8-sig` 读文本，坏 JSON/坏编码降级并记录；单文件 >2MB 跳过并记入 `degradations`。
- output_contract 从 `case.json` 递归发现（键名可为 `output_contract/outputContract/contract/result_schema/expected_output` 等别名）；找不到时用最小字段集兜底并在 `uncertainty` 标 `pending_human`。

## 3. 输出契约（/app/output/result.json）

必须产出，字段如下：

- `case_id`：取 case.json 的 `case_id/id/challenge_id/armiId`；缺失时用输入目录名 + 内容哈希。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实赋值。
- **output_contract 的全部字段原样回填**：叶字段按类型占位（标量 `null`、数组 `[]`、对象 `{}`），Agent 每填一个契约字段即算一次"契约推进"；不改名、不删未知键、不改层级。
- `sources`：证据映射数组，每项 `{locator, quote, kind}`，关键数值/图元逐字引用。
- `uncertainty`：数组，记录不确定项、原因与 `pending_human` 缺口。
- `pending_human`：数组，列出必须人工确认的项（缺字段语义、缺单位、缺误差线定义等）。
- `change_log`：数组，每项 `{ts, seq, step, action, files, contract_delta}`；首次落盘与每次增量改进必记，`contract_delta` 记录该步后的契约填充进度。
- 附加：`errors`、`degradations`、`runtime`、`artifacts`（主产物路径与格式）。

保留键（`case_id/status/sources/uncertainty/pending_human/change_log/errors/degradations/runtime/artifacts`）由本 Skill 维护；若契约与保留键同名，以本 Skill 语义为准并在 `uncertainty` 注明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

阶段 A（0–3 分钟，必须完成，≤4 次请求）：

读输入 → 提取契约 → 在 Skill 根目录运行
`python3 scripts/main.py init --input /input --output /app/output --work /app/work`，
落盘 `result.json` 骨架（`status=partial`，所有契约叶字段已回填占位）与 `work/plan.md`。此后任何崩溃都有合法产物。立即运行
`python3 scripts/main.py contract-report --input /input --output /app/output`
查看契约叶总数，据此规划后续填充顺序。

阶段 B（3–12 分钟，主分析，≤45 次请求）：

按顺序执行：①读数据字典与字段语义 → ②记录清洗口径（缺失/异常/重复/离群，先记录再处理）→ ③统计 → ④按类型与目的选图 → ⑤编码/分面/配色 → ⑥标注与图注 → ⑦导出 SVG+PDF+≥300dpi PNG。每完成一步用
`python3 scripts/main.py update --set '契约字段="值"' --log "..."` 增量落盘并写 change_log；先填契约字段，再补 sources/uncertainty。每填完一轮跑一次 `contract-report` 确认填充进度。

阶段 C（12–15 分钟，收尾，≤10 次请求）：

运行 `python3 scripts/main.py check --input /input --output /app/output --require-contract` 自检；补齐图注/证据/uncertainty 与未填充契约叶，设 `status`，更新 `runtime.finished_at/seconds`，最后再跑一次 check。

请求预算：A ≤4；B ≤45；C ≤10；保留 5 次给失败恢复。每步一次请求做一件事，先读后写，中间产物落 `work/`。

## 5. 数据与图表规则（详见 references/figure-spec.md）

- 清洗：缺失/异常/重复先记录再处理，写清口径，不静默删除或填充；剔除必须保留原始值与规则。
- 选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/小提琴/点图；构成→堆叠/归一化条形；相关趋势→散点/折线；多变量→散点矩阵/相关热图/分面。不把无序分类画成饼图、不把时间序列画成并列柱。
- 编码：优先位置/长度；面积与角度会夸大差异；类别 >6 聚合或分面；量纲差异大分面或标准化；对数轴显式标注。
- 标注：轴标题=变量名(单位)；图例对应数据字典；误差线写明 SD/SEM/CI 并给 n；显著性给 p 值或记号定义；图注脱离正文可读。
- 可读性：Okabe-Ito 色盲安全配色，灰度可辨；字号 ≥7pt；单栏宽 3.4 in。
- 导出：SVG + PDF（字体嵌入）+ ≥300 dpi PNG；中文先探测 CJK 字体，缺字体改英文标签并记录 degradation。

## 6. 自检清单（对应评分维度，逐条可勾选）

- [ ] result.json 合法且含 case_id + 全部契约叶字段 + status/sources/uncertainty/pending_human/change_log（可核验性 / 完整性）
- [ ] 每个关键数值/图元有 locator + 逐字 quote 证据；冲突并存标 source_conflict（可核验性）
- [ ] 图上数值/轴类型与刻度/单位/图例/误差线含义/显著性/色标范围与源数据一致（正确性）
- [ ] 覆盖任务要求全部图表要素，交付绘图代码与图注（完成度）
- [ ] 导出 SVG+PDF+≥300dpi PNG，尺寸匹配栏宽，中文无乱码（发表级导出）
- [ ] 重跑两次统计与图形一致（固定随机种子/排序/布局）（复现性）
- [ ] 缺失/异常/失败均显式说明，无编造（异常处理）
- [ ] 空输入/坏 JSON/缺参数也产出合法 result.json 且退出码 0（健壮性）
- [ ] `contract-report` 显示 filled == total（契约完整变体的硬指标）

交付前运行 `python3 scripts/selftest.py`，确认 SUMMARY ALL PASS。

## 7. 降级策略

- 缺数据：`status=insufficient_evidence`，列出缺口，不编造数值/结论。
- 缺软件（如无 matplotlib/CJK 字体）：用可用子集完成（纯文本统计 + 数据表），`degradations` 标注 limitations，不伪装成完整图。
- 缺 output_contract：最小字段集兜底，`uncertainty` 标 `pending_human`，`_contract_meta.found=false`。
- 写盘失败：降级到最小合法 `result.json`，进程不崩溃（脚本异常也退出码 0）。
- 契约叶无法取值：保留 `null` 并在 `uncertainty` 说明原因与 `pending_human`，绝不编造填值。

## 8. 最小调用示例（示意输出，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/main.py contract-report --input /input --output /app/output
# Agent 完成分析后：
python3 scripts/main.py update --set 'statistics.mean="12.3"' --log "完成描述统计"
python3 scripts/main.py update --set-key 'uncertainty' --set-value '["样本量较小，结论外推需谨慎"]'
python3 scripts/main.py check --input /input --output /app/output --require-contract
python3 scripts/selftest.py
```

示意输出（标注为示例）：`{"case_id":"demo-000000000000","status":"ok","statistics":{"mean":"12.3",...},"sources":[{"locator":"data.csv#L2","quote":"...","kind":"value"}],...}`。
