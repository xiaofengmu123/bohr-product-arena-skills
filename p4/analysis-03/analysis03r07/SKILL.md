---
name: sci-viz-robust
description: 科研数据可视化全流程 Skill（鲁棒性优先）：从 CSV/SQLite/材料 到可投稿图件的清洗、统计、选图、编码、标注、发表级导出，并落盘可复核的 result.json。对空输入、坏 JSON、坏编码、缺参数、超大文件均优雅降级、退出码 0、绝不编造。当需要把科研数据做成可复跑、逐元素可核对的图件时使用。
version: 1.1.0
---

# 科研数据可视化 Skill（鲁棒性优先版）

## 1. 角色与触发时机

- 角色：你是"数据 → 投稿级图件"流水线的执行者。产出不是"建议画什么图"的文字，
  而是真实图件 + 绘图代码 + 图注 + 数据字典 + 证据映射 + 可复核 result.json。
- 触发：输入目录出现 `case.json` + `task.md`，且任务要求对科研数据做清洗、统计、
  可视化并交付可复核结果。
- 不适用：纯文字问答、无数据的画图建议、实验设计或统计方法创新、图像后期美化。

## 2. 输入契约（先发现，再假设）

- 固定路径：`/input/case.json`（任务与 output_contract）、`/input/task.md`（题面）；
  材料可能在 `/input` 下的 `*.csv|*.tsv|*.txt|*.md|*.db|*.sqlite|materials/`。
- 工作目录 `/app/work`，最终产物（含 `result.json`）写到 `/app/output/`。
- 不假设固定文件名/列名/键名；`utf-8-sig` 读文本，逐级降级 gb18030/latin-1；
  坏 JSON/坏编码降级并记录；单文件 >2MB 跳过并记 `degradations`（不读半行）。
- output_contract 提取顺序：①case.json 显式键（`output_contract`/`outputContract`/
  `contract`/`result_schema`/`expected_output` 等）；②无显式键时从 task.md/case.json
  正文表格字段（分析结果 / 质量与复现证据 / 异常说明）反推；③仍无则最小契约兜底
  并在 `pending_human` 注明。

## 3. 输出契约（/app/output/result.json）

必须产出，字段如下：

- `case_id`：取 case.json 的 `case_id/id/challenge_id`；缺失用输入目录名 + 内容哈希。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实赋值。
- **output_contract 的全部字段原样回填**：不改名、不删未知键、不改层级；无法取值
  给 `null`（列表给 `[]`）。无显式契约时回填 `analysis_result` / `quality_evidence` /
  `anomalies` 三组字段。
- `sources`：证据映射数组，每项 `{locator, quote, kind, hash}`；关键数值/图元逐字引用。
- `uncertainty`：数组，记录不确定项、原因；`pending_human`：人工确认缺口（顶层数组）。
- `change_log`：数组，每项 `{ts, seq, step, action, files}`；首次落盘与每次增量改进必记。
- 附加：`errors`、`degradations`、`runtime`、`artifacts`（主产物路径与格式）。

保留键（`case_id/status/sources/uncertainty/pending_human/change_log/errors/
degradations/runtime/artifacts`）由本 Skill 维护；契约与保留键同名时以本 Skill 语义
为准并在 `uncertainty` 注明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 每响应 ≤16384 token）

阶段 A（0–3 分钟，必须完成，≤4 次请求）：
读输入 → 提取契约 → 在 Skill 根目录运行
`python3 -B scripts/main.py init --input /input --output /app/output --work /app/work`，
落盘 `result.json` 骨架（`status=partial`，契约字段为 `null/[]`）、`work/plan.md` 与
`work/manifest.json`。**此后任何崩溃都有合法产物，只做增量改进、不重建空文件。**

阶段 B（3–12 分钟，主分析，≤45 次请求）：
①读数据字典与字段语义 → ②记录清洗口径（缺失/异常/重复）→ ③统计 → ④按类型与目的
选图 → ⑤编码/分面/配色 → ⑥标注与图注 → ⑦导出 SVG+PDF+≥300dpi PNG。每步用
`python3 -B scripts/main.py update ...` 增量落盘并写 change_log；关键结论同时写 sources。

阶段 C（12–15 分钟，收尾，≤10 次请求）：
运行 `python3 -B scripts/main.py check --output /app/output --require-contract`，
补齐图注/证据/uncertainty/pending_human，设 `status`，更新 `runtime.finished_at/seconds`。
保留 5 次请求给失败恢复。每步一次请求做一件事，先读后写，中间产物落 `work/`。

## 5. 数据与图表规则

执行时逐条落实 `references/figure-spec.md`：

- 清洗：缺失/异常/重复**先记录再处理**，写清口径，不静默删除或填充。
- 选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/点图；构成→堆叠/归一化条形；
  相关趋势→散点/折线；多变量→散点矩阵/相关热图/分面。不把无序分类画成饼图、
  不把时间序列画成并列柱。
- 编码：优先位置/长度；面积与角度会夸大差异；类别 >6 聚合或分面；量纲差异大分面
  或标准化；对数轴显式标注。
- 标注：轴标题=变量名(单位)；图例对应数据字典；误差线写明 SD/SEM/CI 并给 n；
  显著性给 p 值或记号定义；图注脱离正文可读。
- 可读性：Okabe-Ito 色盲安全配色，灰度可辨；字号 ≥7pt；单栏宽 3.4 in。
- 导出：SVG + PDF（字体嵌入）+ ≥300 dpi PNG；中文先探测 CJK 字体，缺字体改英文
  标签并记录 degradation。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] result.json 合法且含 case_id + 全部契约字段 + status/sources/uncertainty/
      pending_human/change_log（可核验性）
- [ ] 每个关键数值/图元有 locator + 逐字 quote + hash；冲突并存标 source_conflict
      （可核验性）
- [ ] 图上数值/轴类型与刻度/单位/图例/误差线含义/显著性/色标范围与源数据一致
      （正确性）
- [ ] 覆盖任务要求全部图表要素，交付绘图代码与图注（完成度/完整性）
- [ ] 导出 SVG+PDF+≥300dpi PNG，尺寸匹配栏宽，中文无乱码（发表级导出）
- [ ] 重跑两次统计与图形一致（manifest.json + 固定随机种子/排序/布局）（复现性）
- [ ] 缺失/异常/失败均显式说明，无编造（异常处理）
- [ ] 空输入/坏 JSON/坏编码/缺参数/超大文件也产出合法 result.json 且退出码 0
      （健壮性）

提交前运行 `python3 -B scripts/selftest.py`，逐条核对输出 PASS。

## 7. 降级策略

见 `references/robustness.md`。要点：

- 缺数据：`status=insufficient_evidence`，列出缺口，不编造数值/结论。
- 缺软件（如无 matplotlib/CJK 字体）：用可用子集完成（纯文本统计 + 数据表），
  `degradations` 标注 limitations，不伪装成完整图。
- 缺 output_contract：最小字段集兜底，`pending_human` 标缺口。
- 写盘失败：降级到 work/ 下最小合法 `result.json`，进程不崩溃（脚本异常也退出码 0）。

## 8. 最小调用示例（示意输出，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
# Agent 完成分析后：
python3 -B scripts/main.py update --set 'status="ok"' --log "完成散点矩阵、图注与导出"
python3 -B scripts/main.py update --set-key uncertainty --set-value '["样本量较小，结论外推需谨慎"]'
python3 -B scripts/main.py update --add-source '{"locator":"data.csv#L2","quote":"a,1.2","kind":"value"}'
python3 -B scripts/main.py check --output /app/output --require-contract
```

示意输出（标注为示例）：
`{"case_id":"demo-000000000000","status":"ok","sources":[{"locator":"data.csv#L2","quote":"a,1.2","kind":"value","hash":"..."}], ...}`
