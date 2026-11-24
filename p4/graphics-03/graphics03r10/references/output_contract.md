# result.json 输出契约

本文定义 `/app/output/result.json` 的全部字段。所有字段均为顶级键；`*` 表示必须字段。缺失/非法值会触发 `scripts/main.py check` 的错误或警告。字段与题目输出要求（可编辑源文件、导出件、来源与修改记录）一一对应。

## 顶级结构

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | * | 契约版本，`"1.0.0"` |
| `case_id` | string | * | 取自 `/input/case.json`（其次 `task.md` challenge_id，最后 `unknown`） |
| `title` | string \| null | | 图稿标题 |
| `status` | enum | * | `success` \| `partial` \| `failed`，按实际能力诚实取值 |
| `editable_source_files` | array | * | 分层可编辑源文件（SVG），见下 |
| `export_artifacts` | array | * | 真实 PNG/PDF 导出件，见下 |
| `entities` | array | * | 机制实体清单，见下 |
| `relations` | array | * | 机制关系清单，见下 |
| `legend` | array | * | 图例项，见下 |
| `data_mappings` | array | * | 数据编码映射，见下 |
| `sources` | array | * | 聚合溯源，见下 |
| `uncertainty` | array | * | 歧义/缺口/无来源数值 |
| `pending_human` | array | * | 需人工确认项 |
| `change_log` | array | * | 增量修改记录（首条为骨架落盘） |
| `limitations` | array | * | partial/failed 时的缺口说明 |
| `errors` | array | * | 运行错误记录 |
| `degradations` | array | * | 降级记录（编码/工具/字体） |
| `runtime` | object | * | 见下 |
| `notes` | string | | 补充说明 |
| `materials` | array | | 输入材料发现清单（skeleton 自动写入） |

## editable_source_files[]（可编辑源文件）

```json
{
  "path": "mechanism.svg",
  "format": "svg",
  "generator": "scripts/render.py",
  "bytes": 12345,
  "sha256": "<hex>",
  "layers": ["layer-background", "layer-compartments", "layer-relations",
              "layer-entities", "layer-labels", "layer-legend"]
}
```

要求：`path` 相对 `/app/output/`；SVG 必须 UTF-8 可解析、含 `layer-*` 命名图层、每图元带 `data-id` / `data-kind` / `data-source` 溯源属性。

## export_artifacts[]（导出件）

```json
{
  "path": "mechanism.png",
  "format": "png",
  "width_px": 1920, "height_px": 1080,
  "dpi": 300,
  "font_check": "all_glyphs_ok | missing_font",
  "color_check": "ok | mismatch",
  "status": "generated"
}
```

要求：只记录**真实生成**的文件；未生成的目标格式不得写入，改在 `limitations` 说明。PDF 以 `%PDF-` 魔数为准。

## entities[]（实体）

```json
{
  "id": "ent1", "label": "TNF", "name": "TNF protein",
  "kind": "gene | protein | complex | family | cell_type | compartment | small_molecule | receptor",
  "species": "Homo sapiens | null",
  "synonyms": ["旧名", "同义名"],
  "compartment": "extracellular | membrane | cytosol | nucleus | ...",
  "transmembrane": false,
  "sources": [{"locator": "...", "quote": "...", "material": "..."}]
}
```

命名规范：基因用斜体（`kind=gene`）、蛋白正体（`kind=protein`）；物种前缀明确；同义名归并；复合物与家族成员不混用。

## relations[]（关系）

```json
{
  "id": "rel1", "source": "ent1", "target": "ent2",
  "type": "activation | inhibition | binding | modification | transport | conversion",
  "modifier": "phosphorylation | ubiquitination | ... | null",
  "direction": "forward | reverse | bidirectional | feedback",
  "evidence": "direct | inferred | uncertain",
  "sources": [{"locator": "...", "quote": "...", "material": "..."}]
}
```

语义映射：促进→activation、抑制→inhibition、结合/相互作用→binding、磷酸化/泛素化→modification（附 modifier）、转运/易位→transport、转化/生成→conversion。`evidence=uncertain` 或缺失来源必须同时写入 `uncertainty`。

## legend[] / data_mappings[] / sources[]

- `legend[]`：`{"key", "shape", "description", "applies_to"}`。渲染器会按实际使用的 relation type / entity kind 自动补全，保证图例与图面自洽。
- `data_mappings[]`：`{"target", "channel", "value", "scale", "source"}`。channel ∈ {fill_color, stroke_color, line_width, node_size, shape}；scale 记录编码依据（表达量/显著性/置信度/时间点）。无来源的数值不得标注。
- `sources[]`：`{"element_id", "element_type", "locator", "quote", "material"}`。locator 为材料定位（如 `materials/paper.md#L12`），quote 为逐字片段。

## uncertainty[] / pending_human[] / change_log[] / runtime

- `uncertainty[]`：`{"id", "kind", "description", "affects"}`。kind ∈ {ambiguous_entity, missing_direction, no_source_value, source_conflict}。
- `pending_human[]`：`{"id", "question", "why"}`。
- `change_log[]`：`{"step", "time", "action", "reason"}`。首条必须为 3 分钟内的骨架落盘记录。
- `runtime`：`{"start_iso", "end_iso", "seconds", "requests_used"}`。请求数由平台记录，不自行编造。

## status 判定规则

- `success`：实体/关系/图例/数据映射齐全，SVG + 真实 PNG/PDF 均落盘，check 无错误。
- `partial`：已有部分可交付产物，但存在缺口（缺导出件、歧义未决、来源不全等），limitations 必填。
- `failed`：无法产出任何可交付图稿（如材料缺失到无法建模），limitations 必填。
