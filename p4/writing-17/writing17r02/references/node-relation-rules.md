# 节点与关系规范

本文件是 mindmap.json 的判定规则，Agent 在抽取、归并、连线时必须遵守。目标对应题面五问：抓得准、理得清、连得对、证据可追溯、实际有用。

## 1. 节点抽取（抓得准）
- 只把**核心概念、研究问题、方法、证据、结论、任务、风险、研究空白、局限**做成节点；装饰性、过渡性、寒暄性词语不得成为节点。
- 每个节点 `label` 用材料中的原词或最小改写，保持术语原貌（保留英文缩写，首次出现括注中文）。
- 节点 `kind` 只能从以下取值：`root`、`concept`、`problem`、`method`、`evidence`、`conclusion`、`task`、`risk`、`gap`、`limitation`。事实承载类（evidence/conclusion/method/result/risk）必须带 `evidence`，否则标 `inferred` 并进 uncertainty。

## 2. 层级与粒度（理得清）
- 一个 mindmap.json 只有一个根或极少数并列根；`root` 节点概括主题。
- 同级节点应处于同一抽象粒度，禁止父子同义（子节点只是父节点换个说法）。
- 同类信息归并到同一分支，避免分类交叉与节点重复；重复概念合并为一个节点，用 `note` 记录合并依据。
- 重要信息不得埋在深层：关键结论、风险、任务最多到第 3 层，必要时空缺层数要说明。

## 3. 关系判定（连得对）
- 关系只能从以下取值：`part_of`（属于）、`causal`（导致）、`dependency`（依赖）、`contrast`（对比）、`temporal`（时序）、`citation`（引用）、`support`（支持）、`conflict`（冲突）、`comparison`（比较）、`unknown`（暂未定性）。
- 每条边必须有原文依据：`evidence` 非空，或用 `note` 写明这是概括/推断并标 `inferred`。
- 方向必须正确：`causal` 从因到果，`support` 从证据到结论，`dependency` 从依赖方到被依赖方，`citation` 从论断到被引文献。
- 材料中没有依据的连接一律不画；不确定关系用 `unknown` 并进 uncertainty，不臆断为因果。

## 4. 证据挂接（证据可追溯）
- 证据项固定三字段：`locator`、`quote`、`type`。
  - `locator`：文件路径 + 定位，例如 `materials/paper.pdf#p3`、`materials/notes.md#L12-15`、`materials/supp.xlsx#Sheet1`。
  - `quote`：材料中的**逐字片段**，不得转写、不得概括。
  - `type`：`fact`（原文事实）、`generalization`（多处概括）、`inference`（推断，必须同时标 `inferred`）。
- 每个关键数值、术语、引用、图表、关键论断都要能指回 locator+quote；`evidence_map.json` 与 mindmap.json 的证据项一致。
- 冲突信息不隐藏：同一指标两处不一致，两处都记，并在结果里标 `source_conflict`。

## 5. 可编辑与可复核
- `mindmap.svg` 是矢量主产物，可被 Inkscape/浏览器打开与继续编辑；`mindmap.mm` 可被 FreeMind/Freeplane 打开；`outline.md` 为纯文本大纲。
- 布局确定性：节点按 mindmap.json 中的数组顺序稳定输出，不依赖系统随机数；重复运行逐字节一致。
- 单张导图节点过多（>120）时提示分片或折叠，并在 limitations 说明。

## 6. 最小合法 mindmap.json 示例（示意，非正式答案）
```json
{
  "title": "示例主题",
  "nodes": [
    {"id": "n0", "label": "研究主题", "kind": "root", "parent": null,
     "evidence": [{"locator": "materials/doc.md#L1", "quote": "原文片段", "type": "fact"}]},
    {"id": "n1", "label": "研究问题", "kind": "problem", "parent": "n0",
     "evidence": [{"locator": "materials/doc.md#L3", "quote": "原文片段", "type": "fact"}]}
  ],
  "edges": [
    {"id": "e0", "from": "n1", "to": "n0", "relation": "part_of",
     "evidence": [{"locator": "materials/doc.md#L3", "quote": "原文片段", "type": "fact"}]}
  ]
}
```
