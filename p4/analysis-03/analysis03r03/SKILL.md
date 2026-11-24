---
name: sci-viz-deterministic
description: 确定性科研数据可视化 Skill：从 CSV/SQLite 原始数据到可投稿图件的清洗、统计、选图、编码、标注与发表级导出，内置逐元素证据溯源、输入/输出指纹与字节级可复现自检。当需要把科研数据做成可复跑、可逐元素核对、两次运行严格一致的图件时使用。
version: 2.0.0
---

# 确定性科研数据可视化 Skill

## 1. 角色与触发时机

- 角色：你是「数据 → 投稿级图件」流水线的执行者。产出真实图件 + 绘图代码 + 图注 + 数据字典 + 证据映射，而不是「建议画什么图」的文字。
- 触发：输入目录出现 `case.json` + `task.md`，且任务要求对科研数据做清洗、统计、可视化并交付可复核结果。
- 不适用：纯文字问答、无数据的画图建议、实验设计与统计方法创新、图像后期美化。

## 2. 输入契约

- 固定路径：`/input/case.json`（任务与 output_contract）、`/input/task.md`（题面）；材料可为 `/input` 下 `*.csv|*.tsv|*.txt|*.md|*.db|*.sqlite|*.sqlite3` 或 `materials/` 子目录。
- 工作目录 `/app/work`，最终产物（含 `result.json`）写到 `/app/output/`。
- 先发现再假设：不假设固定文件名/列名/键名；文本用 `utf-8-sig` 优先、逐级降级 gb18030/latin-1；坏 JSON/坏编码降级并记录到 `errors/degradations`；单文件 >2MB 跳过并记入 `degradations`。
- output_contract 从 `case.json` 的 `output_contract/outputContract/contract/result_schema/expected_output` 或 `task.md` 提取；找不到时用最小字段集兜底并在 `uncertainty` 记 `pending_human`。

## 3. 输出契约（/app/output/result.json）

必须产出，字段如下：

- `case_id`：取 case.json 的 `case_id/id/challenge_id`；缺失时用输入目录名 + 内容哈希。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实赋值。
- **output_contract 的全部字段原样回填**：不改名、不删未知键、不改层级；无法取值给 `null`（列表给 `[]`）。
- `sources`：证据映射数组，每项 `{locator, quote, kind}`；关键数值/图元逐字引用。
- `uncertainty`：数组，记录不确定项、原因与 `pending_human` 缺口。
- `change_log`：数组，每项 `{ts, seq, step, action, files}`；首次落盘与每次增量改进必记。
- `determinism`：`{seed, input_fingerprint, output_fingerprint, rerun}`，由 `manifest/check` 维护（见 §7）。
- 附加：`errors`、`degradations`、`runtime`、`artifacts`（主产物路径与格式）。
- 保留键（`case_id/status/sources/uncertainty/change_log/errors/degradations/runtime/artifacts/determinism`）由本 Skill 维护；若契约与保留键同名，以本 Skill 语义为准并在 `uncertainty` 注明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 16384 token）

阶段 A（0–3 分钟，**必须完成**）：读输入 → 提取契约 → 在 Skill 根目录运行
`python3 scripts/main.py init --input /input --output /app/output --work /app/work`，
落盘 `result.json` 骨架（`status=partial`，契约字段为 `null/[]`）与 `work/plan.md`。
**此后任何崩溃都有合法产物。**

阶段 B（3–12 分钟，主分析）：按顺序执行：①读数据字典与字段语义 → ②记录清洗口径
（缺失/异常/重复先记录再处理）→ ③确定性统计 → ④按类型与目的选图 → ⑤编码/分面/配色
→ ⑥标注与图注 → ⑦导出 SVG+PDF+≥300dpi PNG。每完成一步用
`python3 scripts/main.py update --set '...' --log "..."` 增量落盘并写 change_log。
统计可用 `python3 scripts/main.py stats`（纯标准库、确定性）或 matplotlib 重算并核对一致。

阶段 C（12–15 分钟，收尾）：运行 `python3 scripts/main.py manifest --input /input --output /app/output`
写指纹，再运行 `python3 scripts/main.py check --output /app/output --work /app/work --require-contract`
自检，补齐图注/证据/uncertainty，设 `status`，更新 `runtime.finished_at/seconds`。

请求预算：A ≤4 次；B ≤45 次；C ≤10 次；保留 5 次给失败恢复。每步一次请求做一件事，
先读后写，中间产物落 `work/` 下。

## 5. 数据与图表规则（执行时逐条落实，详见 references/figure-spec.md）

- 清洗：缺失/异常/重复**先记录再处理**，写清口径，不静默删除或填充；剔除保留原始值与规则。
- 选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/小提琴/点图；构成→堆叠/归一化条形；相关趋势→散点/折线；多变量→散点矩阵/相关热图/分面。不把无序分类画成饼图、不把时间序列画成并列柱。
- 编码：优先位置/长度；面积与角度夸大差异；类别 >6 聚合或分面；量纲差异大分面或标准化；对数轴显式标注。
- 标注：轴标题=变量名(单位)；图例对应数据字典；误差线写明 SD/SEM/CI 并给 n；显著性给 p 值或记号定义；图注脱离正文可读。
- 可读性：Okabe-Ito 色盲安全配色，灰度可辨；字号 ≥7pt；单栏宽 3.4 in。
- 导出：SVG + PDF（字体嵌入）+ ≥300dpi PNG；中文先探测 CJK 字体，缺字体改英文标签并记录 degradation。

## 6. 自检清单（对应评分维度，详见 references/checklist.md）

- [ ] result.json 合法且含 case_id + 全部契约字段 + status/sources/uncertainty/change_log（可核验性）
- [ ] 每个关键数值/图元有 locator + 逐字 quote 证据；冲突并存标 source_conflict（可核验性）
- [ ] 图上数值/轴类型与刻度/单位/图例/误差线含义/显著性/色标范围与源数据一致（正确性）
- [ ] 覆盖任务要求全部图表要素，交付绘图代码与图注（完成度/完整性）
- [ ] 导出 SVG+PDF+≥300dpi PNG，尺寸匹配栏宽，中文无乱码（发表级导出）
- [ ] 重跑两次统计与图形一致（固定随机种子/排序/布局，`manifest/check` 记录指纹）（复现性）
- [ ] 缺失/异常/失败均显式说明，无编造（异常处理）
- [ ] 空输入/坏 JSON/缺参数也产出合法 result.json 且退出码 0（健壮性）
- [ ] `python3 scripts/selftest.py` 全部 PASS（交付前自证端到端与确定性）

## 7. 确定性与自检（本版重点）

- 固定随机种子 `seed=42`（记录在 `determinism.seed`），不使用系统随机数；类别按「(频次, 标签)」稳定排序，JSON 用 `sort_keys=True`，布局参数显式给定。
- `python3 scripts/main.py manifest --input /input --output /app/output` 计算并回写
  `determinism.input_fingerprint`（输入 sha256 指纹，证明产物对应哪一版输入）与
  `output_fingerprint`（产物指纹）。
- `python3 scripts/main.py check --require-contract` 复核：契约字段齐备、证据映射形状、
  输入指纹匹配、SVG/PDF/PNG 格式与分辨率（PNG 从 pHYs 块读 dpi）。
- `python3 scripts/selftest.py` 在内置微型数据集上端到端跑 init→stats→update→manifest→check，
  断言统计/SVG **两次运行字节级一致**、空目录/坏 JSON/坏编码退出码 0、PNG 分辨率校验正确。
- 同一输入、同一 Python 版本，两次产物（统计、图形、result.json 确定性部分）一致；时间戳
  `ts/started_at/finished_at` 是唯一允许差异的字段，不参与一致性比对。

## 8. 降级策略

- 缺数据：`status=insufficient_evidence`，列出缺口，不编造数值/结论。
- 缺软件（如无 matplotlib/CJK 字体）：用可用子集完成——`scripts/core_impl.py` 提供纯标准库
  统计与 SVG 兜底绘图；`degradations` 标注 limitations，不伪装成完整图。
- 缺 output_contract：最小字段集兜底，`uncertainty` 标 `pending_human`。
- 写盘失败：降级到最小合法 `result.json`，进程不崩溃（脚本异常也退出码 0，仅 `check --fail`
  存在 FAIL 项时退出 1）。

## 9. 最小调用示例（示意输出，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/main.py stats --input /input --work /app/work
# Agent 完成分析后：
python3 scripts/main.py update --set 'status="ok"' --log "完成散点矩阵、图注与导出"
python3 scripts/main.py update --set-key 'sources' --set-value '[{"locator":"data.csv#L2","quote":"...","kind":"value"}]'
python3 scripts/main.py manifest --input /input --output /app/output
python3 scripts/main.py check --output /app/output --work /app/work --require-contract
```

示意输出（标注为示例）：`{"case_id":"demo-000000000000","status":"ok","sources":[{"locator":"data.csv#L2","quote":"...","kind":"value"}], ...}`。

## 10. 边界

- 一个 Skill 只解决「科研表格数据 → 投稿级图件」一类任务；不做实验设计、统计方法创新、论文写作。
- 没有真实材料时输出「材料不足」而非示例填充；正式测试案例由平台提供，本包不自带答案。
- 交付前先运行 `python3 scripts/selftest.py` 在非作者环境（不同机器/不同 Python 版本）复核。
