# evidence_map 结构规范（证据溯源强化）

本 Skill 以 `evidence_map` 为复核核心。任何出现在图中的结论、数值、实体、关系、图例色块，
都必须能在 `evidence_map.claims[]` 或对应列表项的 `locator`+`quote` 中找到逐字来源。

## 1. 定位符（locator）写法

- 文本文件：`materials/<file>.md#L<line>`（行号，1 起）。
- PDF：`materials/<file>.pdf#p<page>`；有解析文本时用 `#p<page>#L<line>`。
- CSV：`materials/<file>.csv#L<line>`（数据行）；表头行也可记 `#header`。
- 材料在 `/input/` 下时，相对 `materials/` 指代其文件名；源文件在 `/input` 根目录时直接写文件名。

## 2. claims[] 字段

| 字段 | 必填 | 说明 |
|-|-|-|
| `claim_id` | 是 | 稳定唯一 id，如 `c01` |
| `text` | 是 | 结论/数值/事实的一句陈述 |
| `kind` | 是 | `conclusion \| data \| entity \| relation` |
| `support_level` | 是 | `direct`（原文直接支持）/ `inferred`（推断）/ `ambiguous`（歧义）/ `not_reported`（材料未报） |
| `locator` | 是 | 见上节 |
| `quote` | 是 | **逐字引用**；无法取得原文写 `not_reported`，不得改写拼接 |
| `source_ref` | 是 | 指向 `sources[].source_id` |
| `maps_to` | 否 | 对应 `entities[]/relations[]/data_mapping[]/legend[]` 的 id 列表，用于双向核验 |
| `confidence` | 否 | `high / medium / low` |
| `note` | 否 | 补充说明、单位、条件、页码偏移 |

## 3. 冲突与未决

- 同一指标两个来源不一致：两处 `claims` 都保留，并在 `conflicts[]` 记
  `{claim_a, claim_b, kind:"source_conflict", note}`。
- 材料缺失、无法判断：记入 `unresolved[]` 与 `pending_human[]`，不估算、不补齐。

## 4. 列表项（entities/relations/data_mapping/legend）的溯源字段

每项至少含：`id`、`source_ref`、`locator`、`quote`。`quote` 与 `claims[]` 中对应的
`quote` 应能逐字对上，`maps_to` 反向指回这些 id。

## 5. 不可违反

- 不虚构 DOI、引用、数值、文件路径；来源不存在就写 `not_reported`/`pending_human`。
- 不把推断写成直接证据：`inferred` 必须写 `note` 说明推断依据。
- 图中每个数字必须能在 `quote` 或材料中定位；找不到就删除该数字并记 `uncertainty`。

## 6. 完整链路示例（示意，非正式案例）

材料 `materials/paper.pdf#p3` 原文含句：「Compound A inhibits kinase B by 40% at 10 µM.」，
`materials/chart.csv#L7` 为 `treatment, inhibition_pct, dose_uM` 的数据行 `A, 40, 10`。

```json
{
  "entities": [
    {"id": "e1", "label": "Compound A", "type": "molecule", "panel": "method",
     "source_ref": "s1", "locator": "materials/paper.pdf#p3",
     "quote": "Compound A inhibits kinase B by 40% at 10 µM."},
    {"id": "e2", "label": "Kinase B", "type": "protein", "panel": "method",
     "source_ref": "s1", "locator": "materials/paper.pdf#p3",
     "quote": "Compound A inhibits kinase B by 40% at 10 µM."}
  ],
  "relations": [
    {"id": "r1", "from": "e1", "to": "e2", "kind": "inhibits", "label": "inhibits",
     "source_ref": "s1", "locator": "materials/paper.pdf#p3",
     "quote": "Compound A inhibits kinase B by 40% at 10 µM."}
  ],
  "data_mapping": [
    {"id": "d1", "entity_id": "e1", "channel": "bar", "value": 40, "unit": "%",
     "source_ref": "s2", "locator": "materials/chart.csv#L7",
     "quote": "A, 40, 10"}
  ],
  "evidence_map": {
    "claims": [
      {"claim_id": "c1", "text": "Compound A inhibits kinase B", "kind": "relation",
       "support_level": "direct", "locator": "materials/paper.pdf#p3",
       "quote": "Compound A inhibits kinase B by 40% at 10 µM.",
       "source_ref": "s1", "maps_to": ["r1", "e1", "e2"], "confidence": "high", "note": ""},
      {"claim_id": "c2", "text": "inhibition is 40% at 10 µM", "kind": "data",
       "support_level": "direct", "locator": "materials/chart.csv#L7",
       "quote": "A, 40, 10", "source_ref": "s2", "maps_to": ["d1"],
       "confidence": "high", "note": "unit: %, dose: µM"}
    ],
    "conflicts": [
      {"claim_a": "c2", "claim_b": null, "kind": "source_conflict",
       "note": "仅当另一来源给出不同抑制率时填写，并保留双方 quote"}
    ],
    "unresolved": []
  }
}
```

`c1`、`c2` 的 `maps_to` 反向指向图元；`r1`、`d1` 的 `locator`+`quote` 与 claim 逐字一致，构成可独立复核的
双向证据链。
