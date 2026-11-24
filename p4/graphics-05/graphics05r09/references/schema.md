# 输出契约与检查清单（references/schema.md）

运行时若 `case.json` 未给出 `output_contract`，使用本规范模式；给出时须**原样回填
其全部键**（缺值给 `null`/`[]`），并叠加下述通用字段。

## 1. 规范 result.json 模式

```json
{
  "case_id": "<case 标识，缺失则 null>",
  "status": "completed | partial | failed",
  "graphical_abstract": {"question": "...", "method": "...",
                          "results": "...", "conclusion": "..."},
  "editable_source": {"path": "/app/output/fig_<case_id>.svg",
                      "format": "svg", "layers": ["layer-frame", "layer-..."],
                      "text_editable": true},
  "exports": [{"format": "png", "path": "...", "status": "rendered|preview_not_rendered",
               "renderer": "cairosvg|rsvg-convert|inkscape|chromium|null",
               "width_px": 1400, "dpi": null}],
  "entities": [], "relations": [], "data_mapping": [], "legend": [],
  "sources": [], "uncertainty": [], "limitations": [],
  "pending_human": [], "change_log": [],
  "validation": {"ok": true, "findings": []}
}
```

- `status` 判定：四个摘要要素齐全且校验无 error → `completed`；有缺项或 warning → `partial`；
  无 case_id 或致命失败 → `failed`。
- 每个 `entities/relations/data_mapping/legend` 条目都必须带 `source`（locator + quote）。
- `exports` 只有真实渲染产物；无渲染器时 `status=preview_not_rendered`，`renderer=null`。

## 2. figure_spec.json 图元模型

`elements` 支持：`panel`（面板）、`box`（框+居中文字）、`text`（独立文字）、
`arrow`（from/to/directed/label，自动裁剪到框边界）、`bar`（数值→高度，带单位与基线）、
`swatch`（图例色块）。缺省坐标由 `svg.resolve_layout` 按面板内网格确定性补齐；
fill 缺省按 Okabe-Ito 色盲安全色板依次分配。文字一律保留为可编辑 `<text>`。

## 3. 逐条自检（对应评分维度）

- [ ] 结果完整性：case_id + 全部 output_contract 字段 + status/sources/uncertainty/change_log
- [ ] 可核验性：每个图元带 `data-source`；sources 有 locator + 逐字 quote
- [ ] 结果正确性：箭头方向与来源句一致；bar 的 value/max/unit 对应原始数据；图例色与编码一致
- [ ] 可编辑源文件：SVG 有 `layer-*` 分组、文字为 `<text>`、无 text-as-path
- [ ] 导出件：PNG/PDF 真实渲染或如实 `preview_not_rendered`
- [ ] 版式：无溢出、字号 ≥8px、填充与白底对比 ≥1.8、灰度/色盲分离可用
- [ ] 稳定性：同输入两次 build 字节一致；change_log 随每次重写递增
- [ ] 异常处理：缺失/歧义进入 uncertainty/limitations/pending_human，不编造
- [ ] 复用性：脚本仅标准库、相对路径调用；`python3 scripts/selftest.py` 退出码 0

## 4. 证据溯源（硬要求）

关键结论/数值/图元都须能指回材料中的逐字片段：`locator` 用 `input/<file>#p<页码>`
或 `#L<行号>` 定位，`quote` 为该片段的原文。同指标两来源不一致时，两者都记并标
`source_conflict`；找不到就写 `not_reported` 并进入 `pending_human`。
