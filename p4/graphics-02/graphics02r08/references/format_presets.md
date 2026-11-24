# figure spec 约定与导出预设

`scripts/figure.py` 的输入是一个 JSON 规范（figure spec），由 Agent 读完材料后书写；脚本把它渲染成 SVG/PNG/PDF。本文件是 spec 的字段约定与可复用导出预设。

## 通用字段
```json
{
  "figure_type": "bar",            // bar|line|scatter|box|heatmap|multipanel|mechanism
  "size_mm": [180, 130],
  "title": "", "x_label": "", "y_label": "",
  "font_size": 8, "background": "#ffffff",
  "errorbar": "SEM",               // SD|SEM|CI|null（写入图注）
  "significance": [{"a":"A","b":"B","mark":"*","level":"p<0.05"}]
}
```

## 数据图 series 约定（bar/line/scatter 通用）
```json
{
  "categories": ["Ctrl", "A", "B"],
  "series": [{
    "label": "WT", "color": "#1f77b4",
    "data": [{"x": "Ctrl", "y": 1.0, "err": 0.1, "n": 3,
              "field": "WT_mean", "row": 2,
              "locator": {"file": "data.csv", "field": "WT_mean", "row": 2},
              "quote": "1.00"}]
  }]
}
```
- `x` 为分类标签（bar）或数值（line/scatter）。
- `err` 为误差长度，与 `errorbar` 含义一致；`n` 为样本量。
- `field`/`row`/`locator`/`quote` 供 SVG 的 `data-*` 属性与 `annotation_checks` 双向溯源。

## 其他图型
- box：`{"boxes":[{"label":"A","values":[1.1,1.2,...]}]}`（脚本用标准库算四分位数）。
- heatmap：`{"row_labels":[...],"col_labels":[...],"values":[[...]],"cmap":"viridis"}`。
- multipanel：`{"cols":2,"panels":[{"figure_type":"bar",...},{...}]}`。
- mechanism：`{"nodes":[{"id":"n1","label":"EGFR","x":10,"y":20,"w":40,"h":16,"shape":"rect|round|ellipse","fill":"#e8f0fe"}],"edges":[{"from":"n1","to":"n2","kind":"activation|inhibition|plain|dashed","label":"..."}]}`。

## 导出预设（可复用，写入 `sources.export_params`）
| 场景 | SVG | PNG | PDF | 备注 |
|---|---|---|---|---|
| 期刊单栏 | 宽度 ~90 mm | 300 dpi，RGB，白底 | 矢量文字、字体嵌入 | 字号 7–9 pt，线宽 0.5–1 pt |
| 期刊双栏 | 宽度 ~180 mm | 300–600 dpi | 矢量 | 字号 8–10 pt |
| 汇报/演示 | 宽度 ~250 mm | 150–300 dpi | 矢量 | 字号 ≥10 pt，高对比配色 |

字体：优先 font-discovery 找中文字体回退；找不到则英文标签或注明乱码风险。色彩空间默认 RGB + 白底，禁用意外透明背景。

## 导出参数检查（写完 result.json 前执行）
- PNG：`selftest.py` 校验 `\x89PNG` 魔数与 pHYs 的 DPI（<300 报问题）。
- PDF：`selftest.py` 校验 `%PDF-` 魔数；人工确认文字可选中、字体嵌入。
- SVG：`selftest.py` 校验 XML 可解析且含 `<text>` 节点。
