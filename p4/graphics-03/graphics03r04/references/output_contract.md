# result.json 输出契约（output_contract）

本文件定义 `/app/output/result.json` 的全部字段、类型、允许取值与校验规则。校验由 `scripts/main.py check` 执行，退出码 0=通过、1=阻断错误、2=仅警告。未知键保留、不删除；缺值给 `null`/`[]`。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `case_id` | string | 是 | 取自 `/input/case.json`；缺文件时填 `"unknown"` 且 status=`input_error`。 |
| `status` | string | 是 | `success` \| `partial` \| `failed` \| `insufficient_evidence` \| `input_error`。 |
| `task_title` | string | 否 | 任务标题（可取自 case.json/task.md）。 |
| `editable_source_files` | array | 是 | 分层可编辑源文件，至少 1 项。 |
| `export_artifacts` | array | 是 | 真实生成的导出件；允许为空数组（工具缺失时）。 |
| `entities` | array | 是 | 结构化实体清单。 |
| `relations` | array | 是 | 结构化关系清单。 |
| `legend` | array | 是 | 图例项清单（与图形实际编码一致）。 |
| `data_mappings` | array | 是 | 数据编码映射（可为空数组）。 |
| `sources` | array | 是 | 来源聚合（可为空数组，但关键元素应覆盖）。 |
| `uncertainty` | array | 是 | 不确定性记录（可为空数组）。 |
| `pending_human` | array | 是 | 人工待决问题（可为空数组）。 |
| `limitations` | array | 是 | 交付缺口；status≠success 时建议非空（否则警告）。 |
| `degradations` | array | 是 | 能力降级逐项记录（可为空数组）。 |
| `errors` | array | 是 | 运行错误（可为空数组）。 |
| `change_log` | array | 是 | 修改记录，至少 1 条，首条应为"骨架落盘"。 |
| `runtime` | object | 是 | 运行元信息。 |

## 2. 可编辑源文件（editable_source_files[]）

```json
{
  "path": "mechanism.svg",
  "format": "svg",
  "layers": ["compartments","relations","entities","labels","legend","data"],
  "generator": "scripts/build_svg.py",
  "note": "可选"
}
```

- `path` 可为绝对路径或相对 `/app/output/` 的路径；`format` 为 `svg`（或 `drawio` 等其他可编辑格式，需说明）。
- `layers` 必须与实际 SVG 中 `<g id="layer-...">` 对应。
- 校验：文件必须真实存在；`format=svg` 时须能被 XML 解析器解析。

## 3. 导出件（export_artifacts[]）

```json
{
  "path": "mechanism.png",
  "format": "png",
  "width": 1600,
  "height": 1200,
  "width_unit": "px",
  "dpi": 150,
  "font_check": "ok",
  "color_check": "ok",
  "note": "可选"
}
```

- `format`：`png` \| `pdf` \| `svg`。
- `font_check` / `color_check`：`ok` \| `warning` \| `missing`，如实记录。
- 校验：文件存在；PNG 魔数 `\x89PNG`、PDF 以 `%PDF-` 开头、SVG 可解析。未真实生成不得记录该项。

## 4. 实体（entities[]）

```json
{
  "id": "e1",
  "label": "EGFR",
  "type": "protein",
  "species": "human",
  "compartment": "membrane",
  "symbol_style": "normal",
  "transmembrane": true,
  "synonyms": ["ErbB1"],
  "evidence_status": "direct",
  "evidence": [{"locator": "materials/paper.md#p3", "quote": "EGFR is activated by EGF"}],
  "display": true
}
```

- `id` 唯一，供 relations 引用；`label` 为图面显示名。
- `type` 允许值：`gene` \| `protein` \| `complex` \| `family` \| `cell_type` \| `compartment` \| `small_molecule` \| `other`。
- `symbol_style`：`italic` \| `normal` \| `bold`；缺省按 type 推导（gene/family→italic，protein→normal，complex→bold）。
- `compartment` 允许值：`extracellular` \| `membrane` \| `cytosol` \| `nucleus` \| `organelle` \| `unknown`（别名自动归一）。
- `evidence_status`：`direct` \| `inferred` \| `missing`；`direct`/`inferred` 必须至少 1 条 evidence 且 `quote` 非空。
- `evidence[]`：每项含 `locator`（材料位置）与 `quote`（逐字片段）。

## 5. 关系（relations[]）

```json
{
  "id": "r1",
  "source": "e1",
  "target": "e2",
  "type": "activation",
  "direction": "forward",
  "modifier": "phosphorylation",
  "evidence_status": "direct",
  "evidence": [{"locator": "materials/paper.md#p4", "quote": "activated ERK promotes MYC"}],
  "display": true
}
```

- `type` 允许值：`activation` \| `inhibition` \| `binding` \| `modification` \| `transport` \| `conversion` \| `unknown`。
- `direction` 允许值：`forward` \| `backward` \| `bidirectional` \| `unknown`；source/target 一律按生物流向书写（上游→下游），direction 记录流向是否明确。
- `modifier`：磷酸化/泛素化等修饰类型（可选）。
- `evidence_status` 与 `evidence[]` 规则同实体。

## 6. 图例（legend[]）与数据映射（data_mappings[]）

```json
{"item": "activation", "encoding": "arrow \u2192", "applies_to": "activation edges"}
```

```json
{"element": "e3", "metric": "expression", "value": "2.1", "unit": "fold",
 "evidence_status": "direct",
 "evidence": [{"locator": "materials/data.csv#L12", "quote": "2.1 fold"}]}
```

- `data_mappings` 中任何带数值/量级的 `value` 必须有 `evidence`（quote 非空）；无来源数值是阻断错误。

## 7. 来源 / 不确定性 / 人工待决

```json
"sources": [{"source_id": "src1", "file": "materials/paper.md", "version": "1", "elements": ["e1","r1"]}]
"uncertainty": [{"element": "r2", "issue": "direction not stated", "basis": "仅见 co-expression，未见调控方向", "status": "unresolved"}]
"pending_human": [{"question": "A 是否直接磷酸化 B？", "affects": "r2", "suggested_action": "核对补充材料图 S3"}]
```

- `uncertainty[].status`：`unresolved` \| `deferred` \| `resolved`。
- `sources[].elements` 列出被该来源支撑的实体/关系 id。

## 8. 降级 / 错误 / 修改记录 / 运行信息

```json
"limitations": ["PDF 导出因无渲染器缺失，仅交付 SVG"],
"degradations": [{"what": "PDF export", "why": "no renderer available", "effect": "无 PDF 预览", "workaround": "交付 SVG，可用浏览器打开"}],
"errors": [],
"change_log": [{"time": "2026-11-22T00:00:00Z", "action": "skeleton", "status": "partial", "detail": "骨架落盘"}],
"runtime": {"started_at": "2026-11-22T00:00:00Z", "finished_at": "2026-11-22T00:14:59Z", "seconds": 899, "requests_used": 40}
```

## 9. 校验规则汇总（scripts/main.py check）

阻断错误（exit 1）：
- 缺 `case_id`、`status`、`change_log`，或 status 不在允许集。
- 实体缺 `id`/`label`/`type`，或 type 非法。
- 关系缺 `source`/`target`/`type`，或 type 非法，或 source/target 未在实体 id 中。
- `evidence_status` 为 direct/inferred 但无 quote 非空的 evidence。
- `data_mappings` 含数值但无 evidence。
- 记录的源文件/导出件路径不存在，或格式魔数不符。

警告（exit 2，不阻断）：status≠success 但 limitations/degradations 为空；missing 元素仍 `display:true`；direction=unknown 未出现在 uncertainty；source/target 引用未知 id（说明后降级）；成功状态但无导出件。
