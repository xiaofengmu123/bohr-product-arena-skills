# evidence_map 字段规范与 locator/quote 约定

本文件是证据溯源（本版核心设计）的规范。目标：任何人拿到 `result.json` 后，不必重跑 Agent，也能从图上任意一个关键元素走回源材料中的逐字片段，并判断该图元是否“编造”。

## 1. evidence_map 总结构

```json
{
  "evidence_map": [
    {
      "element_id": "fig3.bar.TP53.ctrl",
      "element_type": "bar",
      "source_file": "materials/fig3_data.csv",
      "locator": "row=3,col=mean",
      "quote": "TP53,12.4,1.8",
      "value": 12.4,
      "unit": "ng/mL",
      "mapping": "bar_height = value; linear y-scale, 1 unit = 6.45 px",
      "confidence": "exact",
      "conflicts": []
    }
  ]
}
```

`element_id` 在 SVG 中必须能找到对应元素：`<rect id="fig3.bar.TP53.ctrl" data-element="fig3.bar.TP53.ctrl" data-source="materials/fig3_data.csv" data-locator="row=3,col=mean" .../>`。selftest.py 会双向校验：evidence_map 的 element_id 在 SVG 中存在，SVG 的 `data-element` 在 evidence_map 中存在（允许 `node/text/axis/legend` 等无数值元素只在单侧登记，但必须说明）。

## 2. 字段语义

| 字段 | 必填 | 说明 |
|---|---|---|
| `element_id` | 是 | 唯一且稳定，命名 `图名.类型.实体.系列`，如 `fig3.bar.TP53.ctrl` |
| `element_type` | 是 | `bar/point/line/errorbar/edge/node/axis/legend/annotation/text` |
| `source_file` | 是 | 相对 `/input/` 的路径；无文件（如任务说明）可用 `task.md` |
| `locator` | 是 | 精确定位：`row=3,col=mean`、`sheet1!B4`、`paper.pdf#p4`、`§2.3 para2` |
| `quote` | 是 | 源材料逐字子串，不转述、不改写、不截断关键语义 |
| `value` | 否 | 图元编码的数值；示意/机制图的边可为 `"TP53 --| MDM2"` 关系串 |
| `unit` | 否 | 数值单位；无单位填 `null`，不要猜 |
| `mapping` | 是 | 数据→图元编码规则，如 `bar_height = value`、`edge: A 抑制 B` |
| `confidence` | 是 | `exact`（逐字命中）/`inferred`（推断，注明理由）/`conflict`（来源冲突）/`pending`（待人工） |
| `conflicts` | 否 | 数组，元素含 `source_file`/`locator`/`quote`/`note` |

## 3. locator 约定

- CSV/TSV：`row=<1-based数据行>,col=<列名>`；表头行单独标注 `header`。
- 表格工具解析结果：引用 `t_<name>.json` 里的行号，同时在 quote 保留原文。
- PDF：`<文件名>#p<页码>` 或 `<文件名>#p<页码>§<段落序号>`；页码按解析器（pdf-text-tool）输出，不要用文档印刷页码。
- Markdown/文本：`§<章节>¶<段序号>` 或 `行=<1-based行号>`。
- 粘贴文本（无独立文件）：`source_file=todo.md` 或 `case.json#<键路径>`，locator 写键路径。

## 4. quote 规则

1. **逐字**：quote 必须与源材料完全一致（允许包含标点；不允许把 `12.4` 写成 `12.40`）。
2. **可检索**：quote 必须是源文件文本子串；selftest.py 用 `quote in source_text` 校验。
3. **不截断语义**：数值必须连同其单位/表头上下文截取，避免 `12.4` 无法确认是哪一列。
4. **不改写**：源材料写 `TP53` 就不写 `p53`；写 `ng/mL` 就不写 `ng ml⁻¹`。
5. **冲突保留**：同一指标两处不一致（如正文 12.4、表格 12.5），两条都记录，`confidence=conflict`，`conflicts` 互指，不自行取舍。

## 5. 示意/机制图的证据写法

示意/机制图的“数值”多为关系而非数字，evidence_map 用关系串：

```json
{
  "element_id": "mech.edge.TP53.MDM2",
  "element_type": "edge",
  "source_file": "paper.pdf",
  "locator": "paper.pdf#p4§2",
  "quote": "TP53 transcriptionally activates MDM2",
  "value": "TP53 --| MDM2 (activation)",
  "unit": null,
  "mapping": "arrow TP53 -> MDM2, color #1f77b4",
  "confidence": "exact",
  "conflicts": []
}
```

## 6. confidence 判定

- `exact`：quote 逐字命中且 value/mapping 直接来自该 quote。
- `inferred`：value 由多行/多列推导（如 mean 由 n 个复本求均），`mapping` 里写公式，`note` 写依据。
- `conflict`：来源不一致，已保留全部。
- `pending`：无法确认，写入 `pending_human`，不改数。

## 7. 反例（禁止）

| 错误 | 原因 | 正确做法 |
|---|---|---|
| quote 写成 `约12.4` 而源文是 `12.4` | 转述 | 抄原文 `12.4` |
| 缺 source_file 的数值图元 | 无法回溯 | 补 source_file+locator |
| 两处冲突只保留一处 | 隐藏冲突 | 两条都留 + `source_conflict` |
| 把 log 轴画成线性却写 `mapping=linear` | 编码错误 | mapping 写 `log10(y)` |
| 渲染器缺失仍写 PNG 路径 | 伪造产物 | PNG 填 `null` + uncertainty |
