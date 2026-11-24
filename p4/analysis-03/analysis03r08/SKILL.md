---
name: sci-viz-scorecard
description: 科研数据可视化全流程 Skill，以题目评分维度为审计清单，把 CSV/SQLite 原始数据经清洗、统计、选图、编码、标注与发表级导出做成可复核图件，逐条对齐评分维度自检并落盘 result.json。当任务要求对科研表格数据完成清洗、统计、可视化并交付可复核结果时使用。
version: 1.1.0
---

# 科研数据可视化 · 评分维度对齐版

## 1. 角色与触发

- 角色：你是"原始数据 → 可投稿图件 + 可复核证据链"的执行者，交付真实图件、绘图代码、图注、数据字典与证据映射，不交付"建议画什么图"的文字。
- 触发：`/input/` 出现 `case.json` + `task.md`，且任务要求对科研数据做清洗、统计、可视化并交付可复核结果。
- 不适用：纯文字问答、无数据的画图建议、实验设计与统计方法创新、图像后期美化。

## 2. 评分维度对齐（本版设计重心）

5 个评分维度逐条映射到流程步骤、自检项与 result.json 证据字段；每完成一个维度对应的步骤，就在对应字段落一条可核验证据。完整映射见 `references/scorecard.md`。

| 维度(分) | 流程落点 | 自检 | result.json 证据 |
|-|-|-|-|
| 实际效果(40) | 阶段 B 全链 | S1–S4 | sources / artifacts / figure_check / change_log |
| 使用增益(25) | 阶段 B 对照记录 | S5 | quality_gain |
| 创新增益(15) | profile 选图推荐 + 图元一致性校验 | S6 | chart_plan / figure_check |
| 可靠体验(10) | 全流程兜底 | S7–S8 | errors / degradations |
| 生态贡献(10) | 交付物自检 | S9 | artifacts / 文件清单 |

规则：**文字自述不能替代运行证据**；每个关键数值/图元必须能指回源数据的逐字片段。

## 3. 输入契约

- 固定路径：`/input/case.json`（任务与 output_contract）、`/input/task.md`（题面）；材料在 `/input` 下 `*.csv|*.tsv|*.txt|*.json|*.db|*.sqlite|*.sqlite3` 及 `materials/` 内。
- 工作目录 `/app/work`，最终产物（含 `result.json`）写到 `/app/output/`。
- 先发现再假设：不假设固定文件名/列名/键名；文本按 `utf-8-sig→utf-8→gb18030→latin-1` 逐级降级；坏 JSON 捕获不崩溃；单文本文件 >2MB 跳过并记入 `degradations`。
- output_contract 从 `case.json`（键名可为 `output_contract/outputContract/contract/result_schema/expected_output`）或 `task.md` 提取；找不到用最小字段集兜底并记录 `pending_human`。

## 4. 输出契约（/app/output/result.json）

必须产出，字段如下：

- `case_id`：取 `case.json` 的 `case_id/id/challenge_id`；缺失用输入目录名+内容哈希。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实赋值。
- **output_contract 全部字段原样回填**：不改名、不删未知键、不改层级；无法取值给 `null`（列表给 `[]`）。
- `sources`：证据映射数组，每项 `{locator, quote, kind, field}`；关键数值/图元逐字引用；冲突并存 `source_conflict`。
- `uncertainty` / `pending_human`：不确定项与待人工确认缺口。
- `change_log`：`{ts, seq, step, action, files}`；首次落盘与每次增量改进必记。
- 附加：`errors`、`degradations`、`runtime`、`artifacts`、`scorecard`、`quality_gain`、`figure_check`。

保留键（case_id/status/sources/uncertainty/pending_human/change_log/errors/degradations/runtime/artifacts/scorecard/quality_gain/figure_check）由本 Skill 维护；若与契约同名，以本 Skill 语义为准并在 `uncertainty` 注明。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求，单次回复 ≤16384 tokens）

请求预算：A ≤4，B ≤45，C ≤10，保留 5 次给失败恢复。每步一次请求做一件事，先读后写，中间产物落 `work/` 下。

**阶段 A（0–3 分钟，必须完成）**
读输入 → 提取契约 → 在 Skill 根目录运行：

```bash
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/profile.py profile --input /input --output /app/output --work /app/work
```

落盘 `result.json` 骨架（`status=partial`，契约字段 `null/[]`）、`work/plan.md`、`data_dictionary.json`、`chart_plan.json`。此后任何崩溃都有合法产物。

**阶段 B（3–12 分钟，主分析）**
按顺序执行，每完成一步 `python3 scripts/main.py update --set '...' --log "..."` 增量落盘：
① 读数据字典/字段语义 → ② 记录清洗口径（缺失/异常/重复/离群，先记录再处理，不静默删除填充）→ ③ 统计 → ④ 按类型与目的选图 → ⑤ 编码/分面/配色 → ⑥ 标注与图注 → ⑦ 导出 SVG+PDF+≥300dpi PNG。每个关键数值/图元调用 `python3 scripts/main.py add-source --locator ... --quote ... --field ...` 登记证据；图元与源数据一致性用 `python3 scripts/main.py check-figure` 校验。

**阶段 C（12–15 分钟，收尾）**
运行 `python3 scripts/main.py check --output /app/output --require-contract` 自检，补齐图注/证据/uncertainty，回填 `scorecard` 与 `quality_gain`，设 `status`，`python3 scripts/main.py finalize` 更新 `runtime.finished_at/seconds`。

## 6. 图表规则（执行时逐条落实，详见 references/figure-spec.md）

清洗：缺失/异常/重复先记录再处理，剔除必须保留原始值与规则。选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/小提琴/点图；构成→堆叠/归一化条形；相关趋势→散点/折线；多变量→散点矩阵/相关热图/分面；不把无序分类画成饼图、不把时间序列画成并列柱。编码：优先位置/长度，类别>6 聚合或分面，量纲差异大分面或标准化，对数轴显式标注。标注：轴标题=变量名(单位)，误差线写清 SD/SEM/CI 并给 n，显著性给 p 或记号定义。可读性：Okabe-Ito 色盲安全配色，灰度可辨，字号≥7pt，单栏 3.4 in。导出：SVG+PDF(字体嵌入)+≥300dpi PNG；中文先探测 CJK 字体，缺字体改英文标签并记 degradation。

## 7. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] S1 完成度：真实图件+绘图代码+图注齐备，无"建议画柱状图"空话（实际效果）
- [ ] S2 正确性：图元数值/轴类型与刻度/单位/图例/误差线/显著性/色标与源数据一致（实际效果）
- [ ] S3 完整性：覆盖契约全部字段与图表要素（轴、单位、图例、误差线、显著性、色标、图注、面板编号、比例尺、格式分辨率）（实际效果）
- [ ] S4 可核验：每个关键数值有 locator+quote，从源数据到成图可复跑逐元素核对（实际效果）
- [ ] S5 增益可验证：记录与基础 Agent 的对照差异与减少的返工项，不自夸（使用增益）
- [ ] S6 创新证据：chart_plan 给出推荐理由与不推荐原因；figure_check 校验图元-源数据一致（创新增益）
- [ ] S7 稳定：宽/长表、缺失列、类型混杂、中文列名、日期时间、不同规模、重复运行均稳定（可靠体验）
- [ ] S8 异常：缺失/异常/重复/样本不足/类别过多/导出失败/字体缺失/权限不足均显式说明不编造（可靠体验）
- [ ] S9 生态：SKILL.md 说明齐全、脚本标准库可复用、无未声明依赖（生态贡献）
- [ ] 通用：result.json 合法，含 case_id+全部契约字段+status/sources/uncertainty/change_log；空输入/坏 JSON 也退出码 0

## 8. 降级策略

- 缺数据：`status=insufficient_evidence`，列出缺口，不编造数值/结论。
- 缺软件（无 matplotlib/CJK 字体）：用可用子集完成（纯文本统计+数据表），`degradations` 标注 limitations，不伪装完整图。
- 缺 output_contract：最小字段集兜底，`uncertainty`/`pending_human` 标注。
- 写盘失败/脚本异常：降级到最小合法 result.json，进程不崩溃（脚本异常也退出码 0）。

## 9. 最小调用示例（示意输出，非正式案例）

```bash
cd /skills/weekly-skill
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/profile.py profile --input /input --output /app/output --work /app/work
# Agent 完成分析后：
python3 scripts/main.py add-source --locator 'data.csv#L2' --quote '42.6' --kind value --field 'sepal_length_mean'
python3 scripts/main.py update --set 'status="ok"' --log "完成散点矩阵、图注与导出"
python3 scripts/main.py finalize --output /app/output
python3 scripts/main.py check --output /app/output --require-contract
python3 scripts/selftest.py --package .
```

示意结果（标注为示例，非正式案例）：`{"case_id":"demo-000000000000","status":"ok","sources":[{"locator":"data.csv#L2","quote":"42.6","kind":"value","field":"..."}],...}`。
