---
name: sci-viz-evidence
description: 科研表格数据可视化 Skill：从 CSV/SQLite 原始数据到可投稿图件的清洗、统计、选图、编码、标注与发表级导出，并以 evidence_map 逐条记录每条结论的 locator 与逐字 quote、支持机械复核，交付可复跑、逐元素可核对的 result.json。当需要把科研数据做成可复跑、可逐元素核对的图件时使用。
version: 1.1.0
---

# 科研数据可视化（证据溯源强化版）

## 1. 角色与何时使用

- 角色：你是"数据 → 可投稿图件 + 证据链"流水线的执行者，产出真实图件、绘图代码、图注、数据字典与 evidence_map，而不是"建议画什么图"的文字。
- 使用：输入目录出现 `case.json` + `task.md`，且任务要求对科研数据做清洗、统计、可视化并交付可复核结果。
- 不适用：纯文字问答、无数据的画图建议、实验设计或统计方法创新、图像后期美化。

## 2. 输入契约（先发现，再假设）

- 固定路径：`/input/case.json`（任务与 output_contract）、`/input/task.md`（题面）；材料可能在 `/input` 下 `*.csv|*.tsv|*.txt|*.md|*.json|*.db|*.sqlite|*.sqlite3|materials/`。
- 工作目录 `/app/work`，最终产物（含 `result.json`）写到 `/app/output/`。
- 不假设固定文件名/列名/键名；文本按 `utf-8-sig` 读，坏 JSON/坏编码逐级降级（gb18030/latin-1）并记录；单文件 >2MB 跳过并记 `degradations`。
- output_contract 从 `case.json`（键名可为 `output_contract/outputContract/contract/result_schema/expected_output`）或 `task.md` 提取；找不到时用最小字段集兜底并记录 `uncertainty`。

## 3. 输出契约（/app/output/result.json）

必须产出，字段如下：

- `case_id`：取 case.json 的 `case_id/id/challenge_id`；缺失时用输入目录名 + 内容哈希。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实赋值。
- **output_contract 的全部字段原样回填**：不改名、不删未知键、不改层级；无法取值给 `null`（列表给 `[]`）。
- `sources`：证据映射数组，每项 `{locator, quote, kind}`，关键数值/图元逐字引用。
- `evidence_map`：**本版强化**的细粒度证据链（每条结论的 locator + 逐字 quote + 派生口径 + 复核状态），见第 6 节与 `references/evidence-schema.md`。
- `figure_map`：图件逐元素到证据的映射，每项 `{artifact, element, evidence_id, text}`。
- `uncertainty`：数组，记录不确定项、原因与 `pending_human` 缺口。
- `change_log`：数组，每项 `{ts, seq, step, action, files}`；首次落盘与每次增量改进必记。
- 附加：`errors`、`degradations`、`runtime`、`artifacts`（主产物路径与格式）。

保留键（`case_id/status/sources/evidence_map/figure_map/uncertainty/change_log/errors/degradations/runtime/artifacts`）由本 Skill 维护；若契约与保留键同名，以本 Skill 语义为准并在 `uncertainty` 注明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

阶段 A（0–3 分钟，必须完成）：读输入 → 提取契约 → 在 Skill 根目录运行
`python3 scripts/main.py init --input /input --output /app/output --work /app/work`，
立即落盘 `result.json` 骨架（`status=partial`、全部契约字段为 `null/[]`、`evidence_map=[]`）与 `work/plan.md`。此后任何崩溃都有合法产物。

阶段 B（3–12 分钟，主分析）：按顺序执行：①读数据字典与字段语义 → ②记录清洗口径（缺失/异常/重复先记录再处理，不静默删除）→ ③统计 → ④选图 → ⑤编码/分面/配色 → ⑥标注与图注 → ⑦导出 SVG+PDF+≥300dpi PNG。每完成一步用 `python3 scripts/main.py update --set '...' --log "..."` 增量落盘；每产生一条关键结论用 `python3 scripts/main.py evidence add ...` 记录证据；导出后写 `figure_map`。

阶段 C（12–15 分钟，收尾）：运行 `python3 scripts/main.py evidence verify --output /app/output --input /input` 机械复核每条 quote 是否出现在 locator 处，再运行 `python3 scripts/main.py check --output /app/output --require-contract` 自检，补齐图注/证据/uncertainty，设 `status`，更新 `runtime.finished_at/seconds`。

请求预算：A ≤4 次；B ≤45 次；C ≤10 次；保留 5 次给失败恢复。每步一次请求做一件事，先读后写，中间产物落 `work/` 下。

## 5. 数据与图表规则（执行时逐条落实，详见 references/figure-spec.md）

- 清洗：缺失/异常/重复先记录再处理，写清口径，不静默删除或填充；剔除必须保留原始值与规则。
- 选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/小提琴/点图；构成→堆叠/归一化条形；相关趋势→散点/折线；多变量→散点矩阵/相关热图/分面。不把无序分类画成饼图、不把时间序列画成并列柱。
- 编码：优先位置/长度；面积与角度会夸大差异；类别 >6 聚合或分面；量纲差异大分面或标准化；对数轴显式标注。
- 标注：轴标题=变量名(单位)；图例对应数据字典；误差线写明 SD/SEM/CI 并给 n；显著性给 p 值或记号定义；图注脱离正文可读。
- 可读性：Okabe-Ito 色盲安全配色，灰度可辨；字号 ≥7pt；单栏宽 3.4 in。
- 导出：SVG + PDF（字体嵌入）+ ≥300 dpi PNG；中文先探测 CJK 字体，缺字体改英文标签并记录 degradation。

## 6. 证据溯源（本版核心：每条结论可机械复核）

- 每条关键结论（数值、图元、统计、清洗决策、标注）必须生成一条 `evidence_map` 记录，字段：
  `{id, claim, claim_type, locator, quote, observed, kind, derivation, rule, confidence, verified, conflicts}`。
- `locator` 语法（详见 `references/evidence-schema.md`）：
  `file.csv#L12` 第 12 行；`file.csv#r12c3` 第 12 个数据行（不含表头）第 3 列；`notes.md#p3` 第 3 段；`data.db#table=iris&rowid=12` SQLite 行。
- `quote` 必须是源文件中的逐字片段；`evidence verify` 会打开 locator 读取实际文本，把 `quote` 与 `observed` 比对，命中标 `verified=true`，未命中标 `false` 并写回 `observed`，绝不凭印象填。
- 冲突不隐藏：同一指标两个来源不一致时都记录并标 `conflicts`，`verified=false` 且注明 `source_conflict`。
- `figure_map` 把每个图件元素（轴标目、单位、图例项、误差线、显著性、色标范围）指向一条 `evidence_map` 的 `id`，实现"图上每个视觉元素 ↔ 源数据逐字片段"双向可查。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] result.json 合法，含 case_id + 全部契约字段 + status/sources/evidence_map/figure_map/uncertainty/change_log（可核验性）
- [ ] 每条 evidence_map 有 locator + 逐字 quote，`evidence verify` 后 `verified=true`；未命中已标 `false` 并写 observed（可核验性）
- [ ] 图上数值/轴类型与刻度/单位/图例/误差线含义/显著性/色标范围与源数据一致（正确性）
- [ ] 覆盖任务全部图表要素，交付绘图代码与图注（完成度/完整性）
- [ ] 导出 SVG+PDF+≥300dpi PNG，尺寸匹配栏宽，中文无乱码（发表级导出）
- [ ] 重跑两次统计与图形一致（固定随机种子/排序/布局）（复现性）
- [ ] 缺失/异常/失败均显式说明，无编造（异常处理）
- [ ] 空输入/坏 JSON/缺参数也产出合法 result.json 且退出码 0（健壮性）
- [ ] `python3 scripts/selftest.py` 通过（包内自检）

## 8. 降级策略

- 缺数据：`status=insufficient_evidence`，列出缺口，不编造数值/结论。
- 缺软件（如无 matplotlib/CJK 字体）：用可用子集完成（纯文本统计 + 数据表），`degradations` 标注 limitations，不伪装成完整图。
- 缺 output_contract：最小字段集兜底，`uncertainty` 标 `pending_human`。
- 证据无法定位（文件缺失/坏编码/SQLite 表不存在）：该记录 `verified=false` 并写 `observed=null`，不删除记录。
- 写盘失败：降级到最小合法 `result.json`，进程不崩溃（脚本异常也退出码 0）。

## 9. 最小调用示例（示意，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/main.py evidence add --output /app/output \
  --claim "sepal_length 均值 = 5.843" --locator "iris.csv#r1c1" \
  --quote "5.1" --kind "value" --derivation "列 sepal_length 均值, n=150"
python3 scripts/main.py update --set 'status="ok"' --log "完成散点矩阵、图注与导出"
python3 scripts/main.py evidence verify --output /app/output --input /input
python3 scripts/main.py check --output /app/output --require-contract
```

示意输出（标注为示例）：`{"case_id":"demo-000000000000","status":"ok","evidence_map":[{"id":"ev-001","locator":"iris.csv#r1c1","quote":"5.1","verified":true}], ...}`。
