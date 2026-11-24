# 中间表示与可复核性规范（IR Schema）

本 Skill 的差异化设计：不是"数据 → 图"的一跳，而是"数据 → 数据画像 → 图模型 → 图件"的
可核查链条。中间产物结构化落盘，供 `scripts/main.py check` 与评审逐元素核对。

## 1. 三层中间表示（全部位于 /app/work/）

| 文件 | 谁生成 | 内容 | 用途 |
|---|---|---|---|
| `data_profile.json` | `main.py profile`（确定性计算） | 列名、类型、缺失、唯一值、数值统计、离群、重复 | 机器可读的数据事实，后续图元一致性的唯一依据 |
| `figure_model.json` | `main.py plan` 生成候选，Agent 精化 | 每张图的类型、目的、列角色、编码、轴、误差线、显著性、证据 | 图件的声明式蓝图，与渲染代码分离 |
| `figures/*.py` | Agent 按模板写 | 从 figure_model + 源数据渲染图件 | 可复跑代码，不内嵌结论 |

三层的单向依赖：`figure_model` 引用 `data_profile` 中真实存在的列；渲染脚本只读
`figure_model` 与源数据；任何改动都要回到中间产物而不是直接改图。

## 2. `data_profile.json` 字段

```json
{
  "generated_at": "ISO8601",
  "sources": [{"path": "iris.csv", "kind": "csv", "table": null}],
  "tables": {
    "iris.csv": {
      "n_rows": 150, "n_cols": 5, "encoding": "utf-8-sig", "delimiter": ",",
      "columns": [{
        "name": "sepal_length", "dtype": "numeric",
        "n": 150, "n_missing": 0, "n_unique": 35,
        "min": 4.3, "max": 7.9, "mean": 5.84, "std": 0.83,
        "q1": 5.1, "median": 5.8, "q3": 6.4, "iqr": 1.3,
        "n_outliers_iqr": 6, "outlier_bounds": [3.15, 8.35]
      }],
      "n_duplicates": 0
    }
  },
  "degradations": []
}
```

`dtype ∈ {numeric, categorical, datetime, text, empty}`。数值列含均值/标准差/四分位/IQR 离群数；
分类列含 `n_categories` 与 `top_values`。该文件无随机源，重跑逐字节一致。

## 3. `figure_model.json` 字段

```json
{
  "version": 1, "generated_at": "ISO8601", "task_summary": "…",
  "figures": [{
    "figure_id": "f01", "status": "proposed|final",
    "source": "iris.csv",
    "title": "相关热图（iris.csv）",
    "purpose": "relationship",           // distribution/comparison/relationship/trend/composition
    "chart_type": "correlation_heatmap",
    "rationale": "…", "rejected": ["pie"],
    "columns": [{"name": "sepal_length", "role": "matrix"}],
    "axes": {"x": "变量", "y": "变量", "log": false, "limits": [null, null]},
    "error_bars": {"statistic": "SD", "n": 150},   // 可选
    "significance": {"test": "Welch t", "p": null, "markers": "* p<0.05"}, // 可选
    "style": {"palette": "Okabe-Ito", "bins": "auto", "cbar": true},
    "export": {"formats": ["svg", "pdf", "png"], "dpi": 300, "width_in": 3.4},
    "evidence": [{"locator": "iris.csv#L3", "quote": "5.1,3.5,1.4,0.2,setosa", "kind": "value"}]
  }]
}
```

`columns[].name` 必须能在 `data_profile.json` 的 `source` 对应表中找到，否则 `check` 报
`column_not_in_profile`；`chart_type` 必须在允许集合内；`pie` 仅允许构成型。

## 4. 证据溯源（每个关键结论）

- 每个数值/图元给 `{locator, quote, kind}`；`locator` 用 `文件名#行号` 或 `文件名#表#列`，
  `quote` 为源数据逐字片段，`kind ∈ {value, stat, rule, figure_element, source_conflict}`。
- 同一指标两来源不一致时两条都记并标 `source_conflict`，不掩盖。
- 统计值（均值/标准差/四分位）的证据 `quote` 写原始数值或 `data_profile.json` 路径与字段，
  禁止凭空填写数字。

## 5. 可复核性自检（与 `check` 对应）

- [ ] `figure_model.json` 引用的每个列名都在 `data_profile.json` 中存在
- [ ] `result.json` 中每个关键数值都有 locator + 逐字 quote
- [ ] 图上数值、轴类型、单位、图例、误差线、色标范围与 `data_profile`/源数据一致
- [ ] 重跑两次 `profile` → `plan` 结果一致（确定性，无随机源）
- [ ] 渲染脚本只读 figure_model + 源数据，不内嵌结论
- [ ] 缺失/异常/失败显式写 `degradations`/`errors`，不静默删除或编造
