# 确定性与证据溯源：设计取舍

本包相比"参考打法"的核心变体是**自检与确定性**：不是只写规则，而是让确定性成为可执行、可断言的性质，并把墙钟时间等非确定信息从结果载荷中分离出去。

## 1. 为什么要"逐字节一致"
科研复现的常见失败是"隔天重跑得到另一套数字"。原因通常有三类：随机种子未固定、字典/集合遍历顺序未排序、JSON 序列化顺序不稳定、墙钟时间混入结果。本包逐一消除：
- 随机种子固定 `42`；图表颜色/布局固定；
- 所有循环遍历 `sorted(...)` 或有序字典（Python 3.7+ 插入序），不依赖 `set`/`dict` 的任意顺序；
- 所有浮点先 `round(value, 10)` 再序列化，避免平台浮点打印差异；
- `write_json_atomic` 统一 `json.dumps(..., sort_keys=True, ensure_ascii=False, indent=2)`。

## 2. 结果载荷与运行痕迹分离
- `result.json`：只含确定性的 case_id、契约字段、status、sources、uncertainty、change_log、determinism 等。**不含墙钟时间**。
- `runtime.json`：Python 版本、平台、`created_at_utc`、命令行、输入指纹——这些是环境痕迹，供审计，但不参与结果比较。
- `determinism.input_fingerprint`：对全部输入文件字节做 SHA-256，排序稳定，用于校验"两次运行读的是同一批输入"。

## 3. 证据溯源（locator + 逐字 quote）
- `locator` 定位证据：`case.json:case_id`、`<file>:shape`、`<file>:cleaned_table`、`<db>:schema`、`<db>:query`。
- `quote` 是文件中的逐字片段：case_id 的原始 JSON 值、数据文件表头与前几行原文、SQLite 的 PRAGMA 结果、执行过的 SQL 原文。
- 冲突信息不隐藏：同一指标两个来源不一致时两处都记，标 `source_conflict`；无法判断标 `pending_human`。
- 不虚构引用、DOI、数值；缺失写 `not_reported`。

## 4. 诚实标注的边界
- 正态性：只报告精确的 Shapiro-Francia 统计量 W'（Blom 位置 + Q-Q 相关平方）与矩偏度/峰度，并给出保守 flag；**不报告未实现的精确 p 值**，避免伪精度。
- 两样本检验：Welch t 检验的 t、df（Welch–Satterthwaite）、双侧 p 用不完全 beta 函数精确计算；方差齐性用中位数 Levene 检验（F 近似）。
- 图表 PNG 预览由纯标准库确定性栅格化生成，字体仅含数字/`.`/`-`/`+`/`e`/`E`/空格；完整标签在 SVG 中，PNG 仅作形状预览，该限制写入 `degradations`。

## 5. 降级矩阵
| 场景 | 处置 | 退出码 |
|---|---|---|
| 空输入目录 | `insufficient_evidence` + 缺口 | 0 |
| 坏 JSON | `errors` 记录，`input_error`（仅 case.json 坏时） | 0 |
| 坏编码 | utf-8→gb18030→latin-1→replace 降级 | 0 |
| 缺参数 | `--input/--output/--work/--phase` 有默认值 | 0 |
| 缺列/类型不符 | 别名匹配→`errors`→`pending_human` | 0 |
| 依赖缺失 | 全标准库，无第三方依赖 | 0 |
| 查询为空/数值溢出 | `not_reported` 或空集，不静默跳过 | 0 |
| 写盘失败 | 最小合法 result.json | 0 |

## 6. 复用说明
`core_impl.py` 刻意可被 import：下游分析代码可直接复用
`load_table` / `profile_table` / `welch_ttest` / `normality_screen` /
`levene_median` / `make_svg_chart` / `render_chart_png` / `run_query`，
从而避免 Agent 重写易错的统计与绘图代码。
