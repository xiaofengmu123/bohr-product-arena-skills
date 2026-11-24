# result.json 输出契约（output contract）

`/app/output/result.json` 是每个案例的最终可复核结果。字段全部必填（缺值用 `null`/`[]`/`{}`，不改名、不删键）。
`/app/work/spec.json` 是中间机制规格，`build_svg.py` 的输入；两者共享 entities/relations/evidence 结构。

## 1. result.json 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取自 `/input/case.json`（多键回退见 `main.py skeleton`） |
| `status` | string | `success` / `partial` / `failed` / `insufficient_evidence` / `input_error` |
| `title` | string | 图稿标题（可从材料题目或图注概括） |
| `editable_source_files` | list | 分层可编辑源文件（SVG）及其检查结果 |
| `export_artifacts` | list | 真实 PNG/PDF 导出件及尺寸、分辨率、字体、色彩检查 |
| `entities` | list | 实体清单（与 spec.json 一致，含 evidence） |
| `relations` | list | 关系清单（与 spec.json 一致，含 evidence） |
| `legend` | object | 图例项与图面同源生成的说明 |
| `data_mappings` | list | 视觉通道 → 数据含义的映射（含 evidence） |
| `sources` | list | 聚合来源定位（材料文件、版本、页码/图号） |
| `evidence_map` | list | **细粒度证据映射**：每个关键结论一行，含 element_ref/locator/quote/claim |
| `uncertainty` | list | 歧义实体、方向不明、无来源数值、冲突信息 |
| `pending_human` | list | 需人工确认的事项 |
| `limitations` | list | partial/failed 时必填，说明缺口 |
| `degradations` | list | 缺软件/缺字体/缺导出工具等降级记录 |
| `errors` | list | 运行中捕获的非阻断错误 |
| `change_log` | list | 增量修改记录；**首条必须为前 3 分钟的骨架落盘** |
| `runtime` | object | 起止时间、模型请求数、所用工具等（平台有记录则如实引用，不编造分数） |

## 2. 子结构

### editable_source_files 项
```json
{"path": "/app/output/mechanism.svg", "kind": "svg", "format": "svg",
 "layers": ["compartments","relations","entities","labels","legend"],
 "width_px": 1200, "height_px": 520,
 "checks": {"parse": "ok", "layered": true, "editable": true, "font_family": "..."}}
```

### export_artifacts 项
```json
{"path": "/app/output/mechanism.png", "format": "png",
 "width_px": 2400, "height_px": 1040, "dpi": 300,
 "font_check": "ok|missing:<font>", "color_check": "ok|delta:<note>",
 "renderer": "browser-automation|office-renderer", "checks": {"magic": "ok"}}
```
PNG 尺寸由 `main.py check-result` 从 IHDR 自动回填；PDF 校验 `%PDF-` 魔数。

### entity 项
```json
{"id": "E1", "label": "EGFR", "type": "protein",
 "canonical_name": "EGFR (ErbB1)", "synonyms": ["ErbB1", "HER1"],
 "species": "Homo sapiens", "compartment": "membrane", "transmembrane": true,
 "note": "…", "evidence": [{"locator": "…", "quote": "…", "claim": "…"}]}
```
`type` 取值：`gene | protein | complex | family | cell_type | small_molecule | process`。

### relation 项
```json
{"id": "R1", "source": "E1", "target": "E2", "type": "activation",
 "direction": "E1 -> E2", "modifier": "phosphorylation",
 "evidence_strength": "stated|inferred|uncertain|conflict",
 "evidence": [{"locator": "…", "quote": "…", "claim": "…"}]}
```
`type` 取值：`activation | inhibition | binding | modification | transport | conversion | recruitment`。

### evidence 项（硬要求）
- `locator`（string，必填）：材料文件 + 定位，如 `materials/paper.md#p3`、`materials/fig1.png`、`materials/paper.pdf#p4`。不虚构。
- `quote`（string，必填）：**逐字引用**材料原文片段，不得转写/概括后冒充引用；无原文可引时用空串并在 `claim` 标 `not_reported`。
- `claim`（string）：由该 quote 支撑的结论。冲突时在 `uncertainty` 标 `source_conflict`。

### evidence_map 项（细粒度，本版核心）
每个关键结论（实体识别、关系判定、方向、数据编码、图例项）各一行：
```json
{"element_ref": "E1|R1|data_mapping[0]|legend:<key>",
 "kind": "entity|relation|direction|data_mapping|legend",
 "locator": "…", "quote": "…", "claim": "…", "confidence": "high|medium|low"}
```

## 3. 完整性梯度（check-result 的判定）

| 场景 | 判定 |
|---|---|
| status=success | 必须：源文件≥1、导出件≥1、所有产物存在、所有关键元素有 evidence、change_log 非空 |
| status=partial | 允许缺导出件/缺部分 evidence，但 result.json 字段必须齐全，limitations 说明缺口 |
| status=failed/insufficient_evidence/input_error | 字段齐全，errors/limitations 说明原因 |
