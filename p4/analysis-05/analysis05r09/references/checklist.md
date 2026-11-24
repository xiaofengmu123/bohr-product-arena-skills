# 规则与检查清单（references/checklist.md）

本文件是 `SKILL.md` 的补充：给出可复用的方法选择表、字段/清洗规则约定与交付前自查命令。Agent 在阶段 1–4 可随时对照。

## 1. 字段字典约定（work/profile.json）

每个字段记录：`type`（numeric/categorical）、`n_missing`、`n_unique`，数值列附 `stats`（n/mean/sd/median/q1/q3/min/max/se/sum）。

- 分组列：优先 categorical 且 2≤n_unique≤30、非 ID 列；无则取低基数数值列；关键词 `group/class/species/label/treatment/arm/condition` 优先。
- 目标列：数值列，排除分组列与 ID 列（n_unique==行数）。
- 识别 ID/主键列是为了避免把 ID 当数值目标分析。

## 2. 清洗规则约定（work/clean_meta.json）

- 去重：按全部列精确去重，记录 `rows_removed`；不静默删除。
- 缺失：分析时按测试做完全个案（complete cases），逐测试记录 n；不在清洗阶段擅自删除。
- 异常：Tukey IQR（1.5 倍）标记，`row_indices` 落盘，**只标记不删除**。
- 一致性：`rows_after_dedup == original_rows - duplicates_removed`，且 == `cleaned.csv` 数据行数。

## 3. 方法选择表（假设校验）

| 分组数 | 数值比较 | 非参数 | 效应量 |
|---|---|---|---|
| 2 | Welch t（不等方差） | Mann-Whitney U（正态近似 + tie 校正） | Cohen's d / rank-biserial r |
| >2 | 单因素 ANOVA | Kruskal-Wallis（χ² 近似 + tie 校正） | η² |
| 相关 | Pearson r | — | r² |

前提说明必须写入 `uncertainty`：独立性、分布假设、样本量与多重比较校正。p 值不校正时明确声明“未做多重比较校正”。

## 4. 状态判定

- `ok`：分析完成且至少有一个检验/相关结果，无致命错误。
- `partial`：有错误或降级，但已产出部分可复核结果。
- `insufficient_evidence`：无数据、无可分析数值列、或只有单组。
- `input_error`：`case.json` 缺失/无法解析。

## 5. 交付前自查命令

```bash
python3 -B scripts/main.py --help                 # 退出码 0
python3 -B scripts/main.py run --input ./demo/input --output ./demo/output --work ./demo/work
python3 -B scripts/selftest.py --input ./demo/input --output ./demo/output --work ./demo/work --determinism
```

空目录、坏 JSON、缺列三种情况各跑一次；结果：`result.json` 可解析、status 合法、SVG 可被 XML 解析、两次运行字节一致。
