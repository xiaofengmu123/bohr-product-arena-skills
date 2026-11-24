---
name: sci-viz-audit
description: 科研数据可视化流水线 Skill：从 CSV/SQLite 原始数据到可投稿图件（清洗、统计、选图、编码、标注、发表级导出），逐元素可复核、证据可溯源、局限与不确定性显式化、缺数据绝不编造。当需要把科研数据做成可复跑、可核对、可降级的图件并落盘 result.json 时使用。
version: 2.0.0
---

# 科研数据可视化（证据门控 · 降级与不确定性强化版）

## 1. 角色与触发时机

- 角色：你是“数据 → 投稿级图件”流水线的执行者。产出是**真实图件 + 绘图代码 +
  图注 + 数据字典 + 证据映射 + 局限台账**，不是“建议画什么图”的文字。
- 触发：输入目录出现 `case.json` + `task.md`，任务要求对科研数据做清洗、统计、
  可视化并交付可复核结果。
- 不适用：纯文字问答、无数据的画图建议、实验设计/统计方法创新、图像后期美化。
- **本版核心取舍**：每个结论/数值/图元由证据等级门控；缺证据即降级或记
  `pending_human`，最终 `status` 由 `check --auto-status` 按证据自动给出上限，
  禁止手写 `ok` 绕过门控。

## 2. 输入契约（先发现，再假设）

- 固定路径：`/input/case.json`（任务与 output_contract）、`/input/task.md`（题面）；
  材料在 `/input` 下的 `*.csv|*.tsv|*.txt|*.md|*.json|*.db|*.sqlite|materials/`。
- 工作目录 `/app/work`（中间产物），最终产物（含 `result.json`）写到 `/app/output/`。
- 不假设固定文件名/列名/键名；`utf-8-sig` 读文本，坏 JSON/坏编码降级并记录；
  单文件 >2MB 跳过并记 `degradations`；`.xlsx/.parquet` 等非标准库格式记
  `unsupported_binary`，不伪装成已读取。
- output_contract 从 `case.json`（键名可为 `output_contract/outputContract/contract/
  result_schema/expected_output`）或 `task.md` 的 JSON 块/片段提取；找不到时用
  最小字段集兜底并记 `degradations`。

## 3. 输出契约（/app/output/result.json）

必须产出，字段如下：

- `case_id`：取 case.json 的 `case_id/id/challenge_id`；缺失用输入目录名 + 内容哈希。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实赋值且不高于
  证据上限（见 references/audit-checklist.md 第 5 节）。
- **output_contract 全部字段原样回填**：不改名、不删未知键、不改层级；无法取值给
  `null`（列表给 `[]`）。
- `sources`：证据映射数组，每项 `{locator, quote, kind, level}`，level 取
  `verified|inferred|assumed|missing`；关键数值/图元逐字引用。
- `uncertainty` / `pending_human`：不确定项、原因与人工缺口。
- `limitations`：`{kind, level, detail, pending_human, recoverable}` 显式台账。
- `change_log`：`{ts, seq, step, action, files}`；首次落盘与每次增量改进必记。
- 附加：`errors`、`degradations`、`runtime`、`artifacts`（主产物路径/格式/dpi）。

保留键 `case_id/status/sources/uncertainty/pending_human/limitations/change_log/
errors/degradations/runtime/artifacts/_contract_template` 由本 Skill 维护；契约与
保留键同名时以本 Skill 语义为准并在 `uncertainty` 注明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，单响应 ≤16384 token）

**阶段 A（0–3 分钟，必须完成，≤4 次请求）**
读 `/input` → 提取契约 → 在 Skill 根目录运行：

```bash
python3 scripts/main.py init --input /input --output /app/output --work /app/work
```

立即落盘 `result.json` 骨架（`status=partial`，契约字段全为 `null/[]`）与
`work/plan.md`。此后任何崩溃都有合法产物。

**阶段 B（3–12 分钟，主分析，≤45 次请求）**
按序执行，每步用 `update` 增量落盘（见第 5 节）：
① 读数据字典/字段语义并逐字引用 → ② 记录清洗口径（缺失/异常/重复先记录，不静默
删除或填充）→ ③ 统计（连续变量给 n/均值/中位/SD，误差线注明 SD/SEM/CI）→
④ 按类型与目的选图并写推荐/不推荐理由 → ⑤ 编码/分面/配色（位置>长度，避免面积/
角度）→ ⑥ 标注与图注 → ⑦ 导出 SVG+PDF+≥300dpi PNG。用
`python3 scripts/main.py check --output /app/output` 在关键节点自检。

**阶段 C（12–15 分钟，收尾，≤10 次请求）**
运行 `python3 scripts/main.py check --output /app/output --require-contract
--auto-status --fail`，补齐图注/证据/局限，最终确定 `status`，更新
`runtime.finished_at/seconds`。

请求预算：A≤4；B≤45；C≤10；保留 5 次给失败恢复。每步一次请求做一件事，先读后写，
中间产物落 `work/`。

## 5. 增量落盘（scripts/main.py update）

```bash
python3 scripts/main.py update --output /app/output \
  --set 'status="partial"' --log "完成箱线图与图注"
python3 scripts/main.py update --output /app/output \
  --evidence '{"locator":"data.csv#L2","quote":"5.1","kind":"value","level":"verified"}'
python3 scripts/main.py update --output /app/output \
  --limitation '{"kind":"rendering","level":"critical","detail":"无 CJK 字体，改英文标签","pending_human":false,"recoverable":true}'
python3 scripts/main.py update --output /app/output \
  --uncertainty '{"message":"样本量 n=30，外推受限","reason":"scope","level":"warning","pending_human":false}'
python3 scripts/main.py update --output /app/output \
  --artifact '{"path":"figure.svg","format":"svg","role":"main","dpi":null}'
```

`--set` 也接受 JSON 对象或点路径（`--set-key runtime.finished_at --set-value '...'`）。

## 6. 数据与图表规则（详见 references/figure-spec.md）

- 清洗：缺失/异常/重复**先记录再处理**，写清口径，不静默删除或填充；剔除保留原值
  与规则。
- 选图：连续→直方/箱线/小提琴；分类比较→(分组)箱线/小提琴/点图；构成→堆叠/归一化
  条形；相关→散点/相关热图；趋势→折线。**不把无序分类画成饼图、不把时间序列画成
  并列柱**，写明不推荐原因。
- 编码：优先位置/长度；面积/角度会夸大差异；类别>6 聚合或分面；量纲差异大分面或
  标准化；对数轴显式标注。
- 标注：轴标题=变量名(单位)；图例对应数据字典；误差线写明 SD/SEM/CI 并给 n；显著性
  给 p 值或记号定义；图注脱离正文可读。
- 可读性：Okabe-Ito 色盲安全配色且灰度可辨；字号≥7pt；默认单栏 3.4 in。
- 导出：SVG + PDF（字体嵌入）+ ≥300dpi PNG；中文先探测 CJK 字体，缺字体改英文并记
  `degradations`。

## 7. 降级与不确定性（本版重点，详见 references/audit-checklist.md）

- 每个关键结论/数值/图元都要有 `locator`+逐字 `quote`；冲突来源并存并标
  `source_conflict`；指不回的图元要么删除，要么记 `pending_human`。
- `limitations[]` 用 `info|warning|missing|critical|blocked` 分级；`missing/critical`
  使 `status≤partial`，`blocked` 可恢复为 `partial`、不可恢复为
  `insufficient_evidence`。
- `check --auto-status` 按证据自动降级：有 fatal error→`input_error`；无 artifacts→
  `insufficient_evidence`；有 missing/critical 或 pending_human 缺口→`partial`；否则
  `ok`。**禁止写 status="ok" 绕过门控**。
- 缺数据：`status=insufficient_evidence` + 缺口清单，不生成示例图、不编造数值/结论。
- 缺软件（无 matplotlib/CJK 字体）：用可用子集（纯文本统计 + 数据表 + 图注草稿），
  `limitations` 标注，不伪装成完整图。
- 写盘失败：降级到最小合法 `result.json`，进程不崩溃（脚本异常也退出码 0）。

## 8. 自检清单（提交/交付前逐条勾选，对应评分维度）

- [ ] result.json 合法且含 `case_id` + 全部契约字段 + status/sources/uncertainty/pending_human/limitations/change_log
- [ ] 每个关键数值/图元有 locator + 逐字 quote；冲突并存标 source_conflict
- [ ] 图上数值/轴类型与刻度/单位/图例/误差线含义/显著性/色标范围与源数据一致
- [ ] 覆盖任务要求全部图表要素，交付绘图代码与图注
- [ ] 导出 SVG+PDF+≥300dpi PNG，尺寸匹配栏宽，中文无乱码
- [ ] 重跑两次统计与图形一致（固定随机种子/排序/布局）
- [ ] 缺失/异常/失败均显式说明，无编造
- [ ] 空输入/坏 JSON/缺参数也产出合法 result.json 且退出码 0

提交前运行：

```bash
python3 scripts/selftest.py            # 内部自测（空目录/坏JSON/契约/表格/图型/门控）
python3 scripts/main.py check --output /app/output --require-contract --auto-status --fail
```

## 9. 最小调用示例（示意输出，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 scripts/main.py init --input /input --output /app/output --work /app/work
# Agent 完成分析后：
python3 scripts/main.py update --output /app/output --set 'status="ok"' --log "完成图件与图注"
python3 scripts/main.py check --output /app/output --require-contract --auto-status
```

示意输出（标注为示例）：`{"case_id":"demo-0000000000","status":"ok","sources":[...],"limitations":[],"change_log":[...]}`
