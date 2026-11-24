# 证据溯源与数据结构契约

本文件定义 `figure_spec.json`（图面规格）与 `result.json`（最终结果）的字段，以及证据
（locator + 逐字 quote）的书写规则。所有脚本只依赖标准库，按本契约读写，不臆造任何
数值、引用或 DOI。

## 1. 证据溯源规则（科研场景的硬要求）

- 每个实体、关系、数据项、图例项与关键结论都必须携带 `source` 对象：
  `{"locator": "<相对路径>:<行号>", "quote": "<逐字原文>"}`。
- `locator` 指向 `/input` 下的材料文件，或 `/app/work` 下由 `pdftotext` 导出的文本。
  例如 `materials/paper.txt:42` 表示该文件第 42 行；`materials/paper.txt#p3` 表示页码
  定位（页面级 locator 无法逐字回填时，`quote` 留空并标 `verified:false`）。
- `scripts/core_impl.py::quote_from_locator` 会按 `locator` 回读文件并提取**逐字**原文，
  校验通过则写 `verified:true`；读不到则 `verified:false` 且**不得**补写近似文字。
- 冲突不隐藏：同一指标两个来源不一致时，两条都记录，并在 `uncertainty` 标注
  `source_conflict`；缺失则写 `pending_human` 或 `not_reported`。
- 不虚构引用、DOI、数值、导出软件状态或“已渲染”的假图。

## 2. `figure_spec.json`（构建阶段的唯一内容来源）

```json
{
  "case_id": "case-...",
  "title": "单页标题",
  "canvas": {"width": 1600, "height": 1200},
  "panel_order": ["question", "method", "results", "conclusion"],
  "panel_headings": {},
  "graphical_abstract": {"question": [], "method": [], "results": [], "conclusion": []},
  "entities": [
    {"id": "e1", "label": "实体名", "type": "cell|molecule|tissue|material|device|sample|other",
     "panel": "method", "source": {"locator": "materials/paper.txt:42", "quote": "..."}}
  ],
  "relations": [
    {"id": "r1", "from": "e1", "to": "e2", "label": "activates",
     "direction": "directed|undirected|inhibits",
     "source": {"locator": "...", "quote": "..."}}
  ],
  "data": [
    {"id": "d1", "label": "指标名", "value": 3.2, "unit": "fold",
     "visual": "bar", "panel": "results",
     "source": {"locator": "...", "quote": "..."}}
  ],
  "legend": [
    {"key": "arrow", "meaning": "激活", "color": "#0072B2",
     "source": {"locator": "...", "quote": "..."}}
  ],
  "sources": [],
  "uncertainty": [],
  "limitations": []
}
```

要点：

- `entities`/`relations`/`data` 的 `id` 必须稳定、唯一，排序确定（推荐按首次出现顺序）。
- `direction` 只能取 `directed|undirected|inhibits`；方向必须与来源句的因果一致，
  “A 激活 B” 写 `from=A, to=B, direction=directed`，不得反向。
- `data.value` 只能来自材料中的真实数值；没有数值就不写 `value`，改为在
  `uncertainty` 说明，并把该数据项从图中删除或标 `not_reported`。
- `panel` 决定元素落在四面板中的哪一列；未指定时构建器按顺序确定性分配。

## 3. `result.json` 输出契约

`scripts/main.py --stage extract` 先落盘一版 `status=partial` 的合法骨架；后续阶段只做
增量改进。最终 `result.json` 必须包含：

| 字段 | 要求 |
|-|-|
| `case_id` | 与 `/input/case.json` 的 `id`/`case_id` 或 task.md 的 challenge_id 一致 |
| `status` | 严格取 `completed` / `partial` / `failed` |
| 题目 `output_contract` 全部字段 | 原样回填，缺值给 `null`/`[]`，不改名、不删未知键 |
| `sources` | 每项含 `locator` + `quote`（可核验） |
| `uncertainty` | 歧义、缺失、冲突、近似判断 |
| `pending_human` | 需要人工确认的事项 |
| `change_log` | 每次改写的步骤、时间、状态、导出与覆盖率 |

题目未给 `output_contract` 时，使用 `core_impl.CANONICAL_CONTRACT`：`graphical_abstract`
（question/method/results/conclusion）、`editable_source`（svg/layers/text_editable）、
`exports`（png/pdf/renderer/status）、`entities`、`relations`、`data_mapping`、`legend`、
`sources`、`uncertainty`、`change_log`、`limitations`。

## 4. 可编辑源文件与导出件

- 主产物 `fig_<case_id>.svg` 为分层 SVG：命名组 `layer-panels`、`layer-entities`、
  `layer-relations`、`layer-data`、`layer-labels`、`layer-legend`、`layer-annotations`；
  文字全部保留 `<text>`，**禁止**把文字转成 `<path>`。
- 每个图元带 `data-source="locator"` 属性，供评审直接回溯。
- PNG/PDF 为真实渲染预览：脚本按 `rsvg-convert` → `inkscape` → `magick`/`convert` →
  `chromium headless` 顺序尝试；一个都没有则 `exports.status=preview_not_rendered`，
  保留 SVG 并在 `limitations` 说明，**绝不伪造 PNG/PDF**。

## 5. 失败与降级

- 空输入目录：`extract` 正常退出，写 `status=failed` 骨架 + `limitations`，不抛异常。
- 坏 JSON / 坏编码：记录到 `uncertainty`/`limitations`，继续处理其余材料。
- 缺关键数值、实体歧义、文字溢出、字体缺失、画布超限、导出失败：逐条写入
  `limitations`/`uncertainty`，状态降为 `partial`；关键结构缺失才降 `failed`。
- 重复运行：同一输入两次运行，`figure_spec.json` 未改动时 SVG 字节一致；`selftest.py`
  以 `svg_determinism` 检查守护该约束。
