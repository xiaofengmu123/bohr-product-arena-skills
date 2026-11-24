# 规则与检查清单（references/rules.md）

本文件是 `SKILL.md` 的补充：统计口径决策表、降级矩阵与逐条可勾选的交付前检查清单。
运行时不必逐字背诵本文件；Agent 应先调用 `python3 -B scripts/main.py`（或分阶段调用），
再按本文件核对结果。

## 1. 统计方法选择（依据数据结构，不在报告里堆砌全部检验）

| 场景 | 首选方法 | 前提与近似 | 结果键 |
|---|---|---|---|
| 两组数值比较 | Welch t 检验 | 不假设等方差；近正态；样本≥2 | `welch_t` |
| 两组数值比较（稳健） | Mann–Whitney U | 秩和；含结校正的正态近似；样本≥1 | `mann_whitney_u` |
| 多组（≥3）数值比较 | 单因素 ANOVA F | 独立、近正态、方差近似齐；组数≥2 | `anova_f` |
| 多组数值比较（稳健） | Kruskal–Wallis H | 秩检验；含结校正；近似 χ²(k−1) | `kruskal_wallis` |
| 两数值列线性相关 | Pearson r | 线性；逐对完全观测；n≥3；p 经 t(n−2) | `pearson_r/pearson_p` |
| 两数值列单调相关（稳健） | Spearman ρ | 秩相关；p 经 t(n−2) | `spearman_rho/spearman_p` |
| 数值列描述 | n/均值/标准差/中位数/Q1/Q3/最小/最大 | 缺失逐对剔除 | `columns` |

口径约定：

- 名义显著性水平 α=0.05；**不做多重比较校正**（若做多次检验，报告中必须写明）。
- 异常值按 IQR×1.5 标记（`lower_fence`/`upper_fence`），**只标记不删除**。
- 缺失值按列记录、分析时**逐对剔除**，不做任何插补（不伪造数值）。
- 精确重复行默认删除、保留首条，并在 `clean_meta.json` 记录删行数。
- 分组列自动选择：2–15 个水平的 categorical 列；`task.md` 指定分组列时应以 `--group` 覆盖。
- 目标列默认取全部 numeric 列（上限 20）；`task.md` 指定目标列时以 `--targets` 覆盖。

## 2. 降级矩阵（诚实标注，禁止编造）

| 场景 | 处置 | status |
|---|---|---|
| `/input/case.json` 缺失或不可解析 | 记入 `errors`，契约键回填 null | `input_error` |
| 有 case.json 但无可读数据文件 | 记入 `errors`，契约键回填 null | `insufficient_evidence` |
| 数据文件超 8MB / 空表 / 无 numeric 列 | 跳过并记入 `degradations` | `insufficient_evidence` 或 `partial` |
| 列类型不符 / 全空 / 全常量 | 跳过该列，记入 `degradations`，继续其余列 | `partial` |
| 除零 / 空组 / 样本不足 / 方差不适用 | 该检验返回 `skipped` 与原因，不产伪造 p 值 | `partial` |
| 编码异常 | 依次尝试 utf-8-sig / utf-8 / gb18030 / latin-1，成功则记入 `degradations` | 视情况 |
| `output_contract` 缺失 | 用默认三键 `analysis_results/quality_evidence/anomaly_notes` 并记入 `degradations` | 视情况 |
| 环境无 matplotlib/pandas/scipy | 不依赖：统计手写（不完全 beta/gamma），图用纯标准库 SVG | — |
| 无真实材料 / 口径争议 | 写 `pending_human` 请求人工确认，不擅自定论 | `partial` |

## 3. 交付前检查清单（逐条勾选）

- [ ] `/app/output/result.json` 存在、可被严格 JSON 解析（无 NaN/Infinity）。
- [ ] `result.json` 含 `case_id` + 题目 `output_contract` 全部字段（原样回填，未知键不删）。
- [ ] `result.json` 含 `status/sources/uncertainty/pending_human/errors/degradations/change_log/runtime`。
- [ ] `status` 诚实：无数据=`insufficient_evidence`、输入坏=`input_error`、部分失败=`partial`。
- [ ] 前 3 分钟已落盘 `partial` 骨架，之后只做增量改进，不留空 `/app/output`。
- [ ] `clean_meta.json` 的 `n_raw - n_clean == duplicates.rows_removed`；异常只标记不删。
- [ ] 报告每个数字都能指回 `work/stats.json`，无手写数值；检验方法匹配组数与前提。
- [ ] 图元（箱线分位、散点、回归线）来自 `stats.json`，轴标注单位与误差含义。
- [ ] 每个关键结论带 `sources` 的 `locator + quote`（逐字片段），冲突来源均记录。
- [ ] 同一输入两次运行 `stats.json` 与 SVG 字节一致（确定性，无随机数）。
- [ ] 空输入/坏 JSON/缺列/类型不符/除零/编码错误均退出码 0，并在 `errors/degradations` 说明。
- [ ] 不编造：缺失软件/数据 → `pending_human` 或 `not_reported`，绝不虚构数值、DOI、结论。

## 4. 自检脚本

```bash
python3 -B scripts/main.py --help           # 退出码 0
python3 -B scripts/selftest.py              # 全部 PASS，退出码 0
```

`selftest.py` 用合成数据（非正式案例）覆盖：正常三组分析、重复运行确定性、
空数据、坏 JSON、缺 case.json，并验证 result.json 与 SVG 可解析。
