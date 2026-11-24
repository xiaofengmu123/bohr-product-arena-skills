# 规则与检查清单（references）

本文件补充 `SKILL.md` 的细粒度口径，供 Agent 在阶段 2–4 逐条核对；与脚本实现一一对应。

## 1. 统计检验选型规则

| 场景 | 参数检验（主） | 非参数检验（并列报告） | 效应量 |
|---|---|---|---|
| 2 组比较 | Welch t（不等方差，不假设方差齐性） | Mann-Whitney U（秩和，含结校正） | Cohen's d |
| ≥3 组比较 | 单因素 ANOVA F | Kruskal-Wallis H（含结校正） | η² |

- 前提声明固定写：独立性假设；正态性不做正式检验（以偏度/峰度或非参数检验兜底）；多重比较未校正时在 `uncertainty` 声明。
- 除零、空组、样本不足 → 该检验 `status=skipped` + `reason`，不产出 p 值。

## 2. 清洗规则优先级（clean）

1. **去重**：删除完全相同的行（逐行全字段比对），记录删除数。
2. **缺失**：仅删除“分组键缺失”或“所有目标列均缺失”的行；单列缺失保留并计入 `descriptive` 的 n。
3. **异常**：仅标记（IQR 1.5× 或 z 阈值），**永不静默删除**；写入 `outliers` 与 `__outlier_flag` 列。
4. **类型**：数值列尝试转 float，失败记为 NA，不报错。

等式校验：`original_rows − final_rows == Σ rules.dropped`。原始文件不被修改，清洗轨迹全部留痕。

## 3. 证据溯源格式

```json
{"locator": "/input/data.csv#col=group", "quote": "group,target1,target2", "provenance": "input file header (verbatim)"}
```

- `locator`：文件路径 + 可选 `#table=…` 或 `#col=…`。
- `quote`：材料中的**逐字原文片段**（表头、schema 或首行），不转述、不缩写。
- 同一指标两个来源不一致：两个都记，并追加 `source_conflict` 到 `uncertainty`。

## 4. 数字回填口径

- `report.md` 的每个数字由 `render` 从 `work/stats.json` 直接格式化，不出现手写数值。
- 图元（均值/误差/分位）同源 `stats.json`；轴标注误差含义：bar 图 = SEM，box 图 = q1–median–q3、whisker = min–max。
- 重跑 `render` 应得到字节一致的 `report.md` 与 `figure_*.svg`（确定性）。

## 5. 降级矩阵（诚实标注）

| 场景 | 处置 | status |
|---|---|---|
| 缺 case.json 或坏 JSON | `errors` 记录 | `input_error` |
| 有 case 无数据文件 | `errors` 记录缺口 | `insufficient_evidence` |
| 列缺失/全空/类型不符 | `degradations` 记录并跳过该列 | `partial` |
| 除零/空组/样本不足 | 该检验 `skipped` + `uncertainty` | 按总体完成度 |
| 编码异常 | 依次 utf-8-sig/utf-8/latin-1/gbk | 记 `degradations` |
| SQLite 打开失败 | `errors` 记录，继续其它文件 | `partial` |
| 缺 pandas/scipy/matplotlib | 用纯标准库统计与 SVG | 核心交付不受影响 |

## 6. 时间预算纪律（本版重点）

- `scaffold` 写 `work/clock.json`，后续每条命令更新 `elapsed_s/remaining_s/recommendation`。
- 每阶段开始前读 `python3 -B scripts/main.py clock --work /app/work`。
- 决策规则：`remaining < 120s` 跳过可选图直接 `finalize`；`remaining < 30s` 停止新增，只修 `result.json`。
- 任何阶段失败都不得清空 `/app/output`；所有命令退出码为 0，失败写入 `errors`。
