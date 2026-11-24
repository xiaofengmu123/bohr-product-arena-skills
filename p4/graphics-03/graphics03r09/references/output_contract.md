# result.json 输出契约

`/app/output/result.json` 是每个案例的结构化交付结果。顶层字段必须全部存在（可空但不可缺），键名不得改名或删除。评分时会把本契约字段与题目 `output_contract` 原样回填比对。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取自 `/input/case.json`，必填非空 |
| `status` | string | `success` / `partial` / `failed` / `insufficient_evidence` / `input_error` 之一 |
| `title` | string | 图稿标题（可空） |
| `editable_source_files` | list | 分层可编辑源文件：`[{path, format, layers, description}]` |
| `export_artifacts` | list | 导出件：`[{path, format, width, height, dpi, font_check, color_check}]` |
| `entities` | list | 实体规格（见下），每项含 `sources` |
| `relations` | list | 关系规格（见下），每项含 `sources` |
| `legend` | object | 图例与数据编码映射；`build_svg.py` 会按实际使用的项自动生成，保持图面自洽 |
| `data_mappings` | list | `[{visual, semantic, source}]`：颜色/线宽/大小 → 数据含义 |
| `sources` | list | 聚合来源定位：`[{locator, quote, element_ids}]`，元素 → 材料位置 |
| `uncertainty` | list | 歧义/缺口：`[{element_id, issue, decision}]` |
| `pending_human` | list | 需要人工确认的项 |
| `change_log` | list | 增量修改记录，首条必须是 3 分钟内骨架落盘 |
| `limitations` | list | `partial`/`failed` 等非 success 时必填，如实说明缺口 |
| `errors` | list | 运行期错误（可空） |
| `degradations` | list | 降级记录（缺工具/缺字体等） |
| `runtime` | object | 运行元信息（耗时、脚本版本等，可空） |

## 实体对象（entities[]）

```json
{
  "id": "e2",
  "label": "EGFR",
  "kind": "protein | gene | complex | family | cell_type | small_molecule | process | compartment",
  "name_style": "italic | normal",
  "species": "Homo sapiens",
  "aliases": ["ERBB1"],
  "compartment": "extracellular | membrane | cytosol | nucleus",
  "transmembrane": false,
  "sources": [{"locator": "materials/paper.md#p3", "quote": "EGFR 磷酸化"}],
  "uncertain": false
}
```

命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族成员不混用；同名异义必须消歧并在 `uncertainty` 说明。

## 关系对象（relations[]）

```json
{
  "id": "r2",
  "from": "e2",
  "to": "e3",
  "type": "activation | inhibition | binding | modification | transport | conversion",
  "modifier": "phosphorylation",
  "direction": "forward | reverse | bidirectional",
  "evidence": "stated | inferred",
  "sources": [{"locator": "materials/paper.md#p4", "quote": "RAS 被激活"}],
  "uncertain": false
}
```

语义端点映射（由 `build_svg.py` 统一生成，保证图例与实际一致）：

| type | 端点 | 线色 |
|---|---|---|
| activation | 实心箭头 → | 黑 |
| inhibition | T 形端点 ┤ | 黑 |
| binding | 无端点、虚线 | 灰 |
| modification | 圆点 ● | 蓝 |
| transport | 空心箭头 ▷ | 绿 |
| conversion | 菱形 ◆ | 暗红 |

## 诚实性要求

- 每个关键实体/边必须能回溯到材料的**逐字片段**：`locator`（材料文件 + 位置/页码）+ `quote`（原文引用）。
- 冲突信息不隐藏：同一指标两个来源不一致时都记录，标 `source_conflict`。
- 无来源的数值/方向/颜色编码不标注在图面，写入 `uncertainty`。
- 不编造科学数据、评测分数、文件路径或假导出记录；缺工具时 `export_artifacts` 只保留能真实生成的格式，其余写 `degradations`。
