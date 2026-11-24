---
name: sci-viz-lite
description: 科研表格数据（CSV/SQLite）到可投稿图件的精简可视化流水线：发现输入、清洗口径、统计、选图、编码、标注、发表级导出与可复核 result.json。当需要把科研数据做成可复跑、逐元素可核对的图件时使用。
version: 1.0.0
---

# 科研数据可视化 Skill（精简版）

## 1. 角色与触发

- 你是"数据 → 投稿级图件"的执行者，产出真实图件 + 绘图代码 + 图注 + 统计表 + 证据映射，而非"建议画什么图"。
- 触发：`/input/case.json` + `task.md` 存在，且任务要求清洗、统计、可视化并交付可复核结果。
- 不适用：纯文字问答、无数据的画图建议、实验设计/统计方法创新、图像后期美化。

## 2. 输入契约

- 固定路径：`/input/case.json`（任务与 output_contract）、`/input/task.md`；材料在 `/input` 下（`*.csv|*.tsv|*.txt|*.md|*.db|*.sqlite|materials/`）。
- 工作目录 `/app/work`；最终产物（含 `result.json`）写 `/app/output/`。
- 先发现再假设：不假设固定文件名/列名/键名；`utf-8-sig` 读文本，坏编码/坏 JSON 降级并记录；单文件 >2MB 跳过并记 `degradations`。
- output_contract 从 `case.json`（键 `output_contract/outputContract/contract/result_schema/expected_output`）或 `task.md` 提取；找不到用最小字段集兜底并记录。

## 3. 输出契约（/app/output/result.json）

- `case_id`：取 `case.json` 的 `case_id/id/challenge_id`；缺失用输入目录名+内容哈希。
- `status`：`ok|partial|insufficient_evidence|input_error`，诚实赋值。
- **output_contract 全部字段原样回填**：不改名、不删未知键、不改层级；缺值给 `null`/`[]`。
- `sources`：证据映射数组 `{locator, quote, kind}`，关键数值/图元逐字引用。
- `uncertainty`：数组，记录不确定项、原因、`pending_human` 缺口。
- `change_log`：数组 `{ts, seq, step, action, files}`；首次落盘与每次增量改进必记。
- 附加：`errors`、`degradations`、`runtime`、`artifacts`（主产物路径与格式）。

保留键 `case_id/status/sources/uncertainty/change_log/errors/degradations/runtime/artifacts` 由本 Skill 维护；与契约重名时以本 Skill 语义为准并在 `uncertainty` 注明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 16384 token）

**阶段 A（0–3 分钟，≤4 请求，必须完成）**
1. 读 `/input` → 提取契约 → 运行
   `python3 scripts/main.py init --input /input --output /app/output --work /app/work`
   立即落盘 `result.json` 骨架（`status=partial`、契约字段 `null/[]`）+ `work/plan.md`。此后任何崩溃都有合法产物。
2. 运行 `python3 scripts/viz.py inspect --input /input --work /app/work` 生成数据清单（表/列/类型/样本/缺失）。

**阶段 B（3–12 分钟，≤45 请求，主分析）**
1. 读数据字典与字段语义 → 定清洗口径（缺失/异常/重复，先记录再处理，不静默删除）。
2. 统计（describe/分组/相关）。
3. 按类型与目的选图（见 references/figure-spec.md）。
4. 运行 `python3 scripts/viz.py plot ...`（`--kind/--x/--y/--group/--target/--title`）产出图件+统计+图注+代码。
5. 核对图元与源数据一致 → `python3 scripts/main.py update --set '...' --log "..."` 增量落盘契约字段、`sources`（locator+quote）、`uncertainty`。

**阶段 C（12–15 分钟，≤10 请求，收尾）**
1. `python3 scripts/main.py check --output /app/output --require-contract` 自检。
2. 补齐图注/证据/uncertainty，设 `status`，更新 `runtime.finished_at/seconds`。
3. 空输入/坏 JSON/缺参数也须产出合法 `result.json` 且退出码 0。

## 5. 关键规则（详见 references/figure-spec.md）

- 清洗：缺失/异常/重复先记录再处理，剔除必须保留原始值与规则。
- 选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/小提琴/点图；构成→堆叠/归一化条形；相关趋势→散点/折线；多变量→散点矩阵/相关热图/分面。不把无序分类画饼图、不把时间序列画并列柱。
- 编码：优先位置/长度；面积与角度会夸大差异；类别 >6 聚合或分面；量纲差异大分面或标准化；对数轴显式标注。
- 标注：轴=变量名(单位)；图例对应数据字典；误差线写明 SD/SEM/CI 并给 n；显著性给 p 值或记号定义；图注脱离正文可读。
- 可读性：Okabe-Ito 色盲安全配色、灰度可辨；字号 ≥7pt；单栏宽 3.4 in。
- 导出：SVG+PDF（字体嵌入）+≥300 dpi PNG；中文先探测 CJK 字体，缺字体改英文标签并记 degradation。

## 6. 自检清单（对应评分维度）

- [ ] result.json 合法：含 case_id + 全部契约字段 + status/sources/uncertainty/change_log（可核验性）
- [ ] 每个关键数值/图元有 locator+quote；冲突并存 `source_conflict`（可核验性）
- [ ] 图上数值/轴类型/刻度/单位/图例/误差线含义/显著性/色标范围与源数据一致（正确性）
- [ ] 覆盖任务全部图表要素，交付绘图代码+图注（完成度/完整性）
- [ ] 导出 SVG+PDF+≥300dpi PNG，尺寸匹配栏宽，中文无乱码（发表级导出）
- [ ] 重跑两次统计与图形一致（固定种子/排序/布局）（复现性）
- [ ] 缺失/异常/失败均显式说明，无编造（异常处理）
- [ ] 空输入/坏 JSON/缺参数也产出合法 result.json 且退出码 0（健壮性）

## 7. 降级策略

- 缺数据：`status=insufficient_evidence`，列缺口，不编造数值/结论。
- 缺软件（无 matplotlib/CJK 字体）：用可用子集完成（纯文本统计+数据表 CSV），`degradations` 标注 limitations，不伪装成完整图。
- 缺 output_contract：最小字段集兜底，`uncertainty` 标 `pending_human`。
- 写盘失败：降级最小合法 `result.json`，进程不崩溃（脚本异常也退出码 0）。

## 8. 最小调用示例（示例，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/viz.py inspect --input /input --work /app/work
python3 scripts/viz.py plot --input /input --kind auto --output /app/output --prefix fig1
python3 scripts/main.py update --set 'status="ok"' --log "完成清洗、统计、图件与导出"
python3 scripts/main.py check --output /app/output --require-contract
python3 scripts/selftest.py
```
