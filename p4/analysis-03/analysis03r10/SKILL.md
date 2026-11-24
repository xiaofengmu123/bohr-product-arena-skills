---
name: sci-viz-ir
description: 科研表格数据可视化 Skill：按“数据画像 → FigureIR 中间表示 → 先校验后渲染 → 发表级导出”的顺序，把 CSV/SQLite 原始数据做成可逐元素复核的投稿图件，并落盘可复核的 result.json。当需要把科研数据做成可复跑、可核对的图件时使用。
version: 1.1.0
---

# 科研数据可视化（FigureIR 校验流水线）

## 1. 角色与触发时机

- 角色：你是"数据 → 投稿级图件"流水线的执行者。交付物是真实图件 + 绘图代码 + 图注 + 数据字典 + FigureIR 证据映射，而不是"建议画什么图"的文字。
- 触发：输入目录出现 `case.json` + `task.md`，且任务要求对科研数据做清洗、统计、可视化并交付可复核结果。
- 不适用：纯文字问答、无数据的画图建议、统计方法创新、实验设计、图像后期美化。

## 2. 输入契约（先发现，再假设）

- 固定路径：`/input/case.json`（任务与 output_contract）、`/input/task.md`（题面）；材料可能在 `/input` 下的 `*.csv|*.tsv|*.txt|*.md|*.json|*.db|*.sqlite|*.sqlite3|materials/`。
- 工作目录 `/app/work`，最终产物（含 `result.json`）写到 `/app/output/`。
- 不假设固定文件名/列名/键名；文本用 `utf-8-sig` 读并降级到 `utf-8/gb18030/latin-1`；坏 JSON/坏编码降级并记入 `degradations`；单文件 >2MB 跳过并记录。
- output_contract 从 `case.json`（键名可为 `output_contract/outputContract/contract/result_schema/expected_output`）或 `task.md` 提取；找不到时用最小字段集兜底并记录。

## 3. 输出契约（/app/output/result.json）

必须产出，字段如下：

- `case_id`：取 case.json 的 `case_id/id/challenge_id`；缺失时用输入目录名 + 内容哈希。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实赋值。
- **output_contract 的全部字段原样回填**：不改名、不删未知键、不改层级；无法取值给 `null`（列表给 `[]`）。
- `sources`：证据映射数组，每项 `{locator, quote, kind}`，关键数值/图元逐字引用。
- `uncertainty`：数组，记录不确定项、原因与 `pending_human` 缺口。
- `change_log`：数组，每项 `{ts, seq, step, action, files}`；首次落盘与每次增量改进必记。
- 附加：`errors`、`degradations`、`runtime`、`artifacts`（主产物路径与格式）。

保留键（`case_id/status/sources/uncertainty/change_log/errors/degradations/runtime/artifacts`）由本 Skill 维护；若契约与保留键同名，以本 Skill 语义为准并在 `uncertainty` 注明。

## 4. 中间表示 FigureIR（本版差异化核心）

与"直接写图"的流水线不同，本 Skill 强制经过一个声明式中间表示 `work/ir.json`。它把每个视觉元素（轴、刻度、标记、编码、图例、误差线、显著性、图注）在渲染前写成一棵 JSON 树，每个叶节点携带 `source`（locator + 逐字 quote）。渲染脚本只消费 IR，不直接读原始数据——这样"图上每个元素对应源数据哪一行"在渲染前即可机器校验，而非渲染后靠肉眼核对。

IR 顶层结构（完整 schema 见 `references/ir-schema.md`）：

```json
{
  "case_id": "...",
  "data_profile": {"columns": [...], "cleaning": [...], "n": ...},
  "figures": [{"id": "fig1", "chart": "...", "purpose": "...",
    "encodings": [{"visual": "position-x", "field": "...", "type": "...", "source": {...}}],
    "annotations": [...], "caption": "...", "export": {"svg": "...", "pdf": "...", "png": "..."}}],
  "evidence_map": [...]
}
```

校验顺序（先校验，后渲染）：① 数据契约（列存在、类型可推断、清洗口径已记录）→ ② IR schema（每图有 chart/purpose/encodings/annotations/caption/export，encodings 有 source）→ ③ 渲染产物（SVG 可解析、PDF 以 `%PDF-` 开头、PNG ≥300dpi）→ ④ result.json（契约字段齐 + status/sources/uncertainty/change_log）。任一层失败即回退到 `partial`，不伪造。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求）

阶段 A（0–3 分钟，必须完成，≤4 次请求）：读输入 → 提取契约 → 在 Skill 根目录运行
`python3 scripts/main.py init --input /input --output /app/output --work /app/work`，
落盘 `result.json` 骨架（`status=partial`，所有契约字段为 `null/[]`）与 `work/plan.md`。此后任何崩溃都有合法产物。

阶段 B（3–12 分钟，主分析，≤45 次请求）：① 读数据字典与字段语义 → ② 记录清洗口径（缺失/异常/重复先记录再处理）→ ③ 统计 → ④ 选图 → ⑤ 编码/分面/配色 → ⑥ 标注与图注 → ⑦ 生成 FigureIR 并渲染导出 SVG+PDF+≥300dpi PNG。每完成一步用 `python3 scripts/main.py profile` 刷新数据画像，并把增量写回 `result.json` + `change_log`。

阶段 C（12–15 分钟，收尾，≤10 次请求）：运行 `python3 scripts/main.py check --output /app/output --require-contract` 自检，补齐图注/证据/uncertainty，设 `status`，更新 `runtime.finished_at/seconds`。

请求预算：A ≤4；B ≤45；C ≤10；保留 5 次给失败恢复。每步一次请求做一件事，先读后写，中间产物落 `work/`。

## 6. 数据与图表规则（执行时逐条落实，详见 references/figure-spec.md）

- 清洗：缺失/异常/重复先记录再处理，写清口径，不静默删除或填充；剔除必须保留原始值与规则。
- 选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/小提琴/点图；构成→堆叠/归一化条形；相关趋势→散点/折线；多变量→散点矩阵/相关热图/分面。不把无序分类画成饼图、不把时间序列画成并列柱。
- 编码：优先位置/长度；面积与角度会夸大差异；类别 >6 聚合或分面；量纲差异大分面或标准化；对数轴显式标注。
- 标注：轴标题=变量名(单位)；图例对应数据字典；误差线写明 SD/SEM/CI 并给 n；显著性给 p 值或记号定义；图注脱离正文可读。
- 可读性：Okabe-Ito 色盲安全配色，灰度可辨；字号 ≥7pt；单栏宽 3.4in。
- 导出：SVG + PDF（字体嵌入）+ ≥300 dpi PNG；中文先探测 CJK 字体，缺字体改英文标签并记录 degradation。

## 7. 自检清单（逐条可勾选，对应评分维度）

- [ ] result.json 合法且含 case_id + 全部契约字段 + status/sources/uncertainty/change_log（可核验性）
- [ ] 每个关键数值/图元有 locator + 逐字 quote 证据；冲突并存标 source_conflict（可核验性）
- [ ] 图上数值/轴类型与刻度/单位/图例/误差线含义/显著性/色标范围与源数据一致（正确性）
- [ ] 覆盖任务要求全部图表要素，交付绘图代码与图注（完成度/完整性）
- [ ] 导出 SVG+PDF+≥300dpi PNG，尺寸匹配栏宽，中文无乱码（发表级导出）
- [ ] 重跑两次统计与图形一致（固定随机种子/排序/布局）（复现性）
- [ ] 缺失/异常/失败均显式说明，无编造（异常处理）
- [ ] 空输入/坏 JSON/缺参数也产出合法 result.json 且退出码 0（健壮性）

## 8. 降级策略（详细判定见 references/robustness.md）

- 缺数据：`status=insufficient_evidence`，列出缺口，不编造数值/结论。
- 缺软件（如无 matplotlib/CJK 字体）：用可用子集完成（纯文本统计 + 数据表），`degradations` 标注 limitations，不伪装成完整图。
- 缺 output_contract：最小字段集兜底，`uncertainty` 标 `pending_human`。
- 写盘失败：降级到最小合法 `result.json`，进程不崩溃（脚本异常也退出码 0）。
- status 诚实赋值：`ok`（全部齐备无 FAIL）/ `partial`（部分完成或 check 有 FAIL/WARN，必须写明边界）/ `insufficient_evidence` / `input_error`；禁止为凑 ok 而改数、删料、藏异常。

## 9. 最小调用示例（示意输出，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 scripts/main.py init --input /input --output /app/output --work /app/work
python3 scripts/main.py profile --input /input --output /app/output --work /app/work
# Agent 完成分析并生成 FigureIR + 图件后：
python3 scripts/main.py check --output /app/output --require-contract
```

示意输出（标注为示例）：`{"case_id":"demo-000000000000","status":"ok","sources":[{"locator":"data.csv#L2","quote":"...","kind":"value"}], ...}`。
