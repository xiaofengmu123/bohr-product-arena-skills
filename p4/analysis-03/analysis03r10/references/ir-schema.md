# FigureIR 中间表示与校验顺序

本文件定义本 Skill 的声明式中间表示（FigureIR）与四层校验顺序。渲染脚本只消费 `work/ir.json`，不直接读原始数据；因此"图上每个元素对应源数据哪一行"可在渲染前机器核对。

## 1. 顶层结构

```json
{
  "case_id": "challenge-...",
  "task_hint": "从 task.md 提取的任务要求要点（可选）",
  "data_profile": {
    "tables": [
      {
        "name": "data.csv",
        "kind": "csv|sqlite_table",
        "n_rows": 150,
        "n_cols": 5,
        "columns": [
          {
            "name": "sepal_length",
            "dtype": "numeric",
            "missing": 0,
            "unique": 35,
            "stats": {"mean": 5.84, "std": 0.83, "min": 4.3, "q1": 5.1,
                      "median": 5.8, "q3": 6.4, "max": 7.9, "outliers_iqr": 0},
            "source": {"locator": "data.csv#header", "quote": "sepal_length", "kind": "column"}
          }
        ]
      }
    ],
    "cleaning": [
      {"rule": "drop_duplicate", "target": "data.csv", "count": 0,
       "kept_raw": true, "note": "无重复行，未删除"}
    ]
  },
  "figures": [
    {
      "id": "fig1",
      "chart": "box",
      "purpose": "distribution_compare",
      "title": null,
      "encodings": [
        {"visual": "position-x", "field": "species", "type": "categorical",
         "source": {"locator": "data.csv#col5", "quote": "species", "kind": "column"}},
        {"visual": "position-y", "field": "sepal_length", "type": "numeric",
         "source": {"locator": "data.csv#col1", "quote": "sepal_length", "kind": "column"}}
      ],
      "annotations": [
        {"kind": "axis", "axis": "y", "label": "sepal_length (cm)", "source": {...}},
        {"kind": "errorbar", "metric": "SD", "n": 50, "source": {...}},
        {"kind": "caption", "text": "图注正文（脱离正文可读）", "source": {...}}
      ],
      "export": {"svg": "fig1.svg", "pdf": "fig1.pdf", "png": "fig1.png",
                 "dpi": 300, "width_in": 3.4, "font_min_pt": 7}
    }
  ],
  "evidence_map": [
    {"key": "fig1.mean.sepal_length", "locator": "data.csv#L2",
     "quote": "5.1,3.5,1.4,0.2,Iris-setosa", "kind": "value"}
  ]
}
```

## 2. 字段约束

- `encodings[*].visual` 取值限于 `position-x|position-y|length|color|shape|facet|area|angle`；本 Skill 规定：`area/angle` 仅当所有比较值 ≥0 且任务明确要求时才允许，且必须在 `annotations` 里写明"面积/角度编码会放大差异"。
- `encodings[*].source` 与 `evidence_map[*]` 必含 `locator`（文件#行/列定位）与 `quote`（材料逐字片段）；`quote` 不得为空，不得改写数值。
- `figures[*].export` 必填 `svg/pdf/png/dpi/width_in/font_min_pt`；缺任何一项，`check` 判定该图未完成并建议回退 `partial`。
- 冲突证据并存：同一指标两处来源不一致时，`evidence_map` 各记一条并加 `"conflict": true`，同时在 `result.json.sources` 标 `source_conflict`。

## 3. 四层校验顺序（先校验，后渲染）

| 层 | 校验对象 | 通过条件 | 失败动作 |
|---|---|---|---|
| L1 数据契约 | data_profile | 每个字段有 dtype/stats；清洗口径已记录；列存在 | 记 `errors`，回退 `partial` |
| L2 IR schema | figures/encodings | 每图有 chart/purpose/encodings/annotations/caption/export；encoding 有 source | 记 `errors`，不渲染 |
| L3 渲染产物 | SVG/PDF/PNG | SVG 可被 `xml.etree` 解析；PDF 以 `%PDF-` 开头；PNG 有 ≥300dpi 标记 | 记 `errors`，导出失败项留空并标注 |
| L4 result.json | 契约字段 + 保留键 | 契约全回填；status/sources/uncertainty/change_log 存在且非空结构 | 记 `errors`，按最小字段集兜底 |

顺序不可颠倒：L1/L2 未过就渲染，等于把未核对的数据直接画成图。`scripts/main.py check` 按 L1→L2→L3→L4 执行，逐层报告 PASS/FAIL/WARN。

## 4. 与 result.json 的关系

- `result.json.sources` 是 `evidence_map` 的对外投影；`result.json.artifacts` 是 `figures[*].export` 的落盘清单。
- IR 是过程工件，留在 `work/ir.json`，用于复核与重跑；`result.json` 是交付工件，留在 `/app/output/`。
- 重跑一致性：两次运行 `profile` 必须得到相同的 dtype/stats/outlier 判定（固定排序、固定分位数算法、固定随机种子；本 Skill 不使用随机采样）。
