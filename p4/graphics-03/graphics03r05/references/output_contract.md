# result.json 输出契约（output_contract）

`/app/output/result.json` 是每个案例的最终结构化交付物。它必须是一个 UTF-8 JSON 对象，
**下面 18 个顶层字段全部存在**（缺失值用 `null`/`[]` 占位，不改名、不删未知键），
并且与 `/app/output/` 下的实际文件、`spec.json` 三方路径互相对应。

## 顶层字段（全部必填）

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取自 `/input/case.json`（键 `case_id`/`id`/`caseId`，缺失时回退文件名去扩展名）。非空。 |
| `status` | enum | `success` \| `partial` \| `failed`。诚实取值：闭环完成=success；有可交付部分但缺导出/缺证据=partial；无法产出主产物=failed。 |
| `title` | string/null | 案例或图稿标题。 |
| `task_summary` | string/null | 任务一句话概述。 |
| `editable_source_files` | list | 分层可编辑源文件。每项 `{path, format, layers, tool}`；`path` 相对 `/app/output` 或绝对路径，文件必须真实存在。 |
| `export_artifacts` | list | 真实导出件。每项 `{path, format, width, height, dpi, font_check, color_check, rendered_by}`。缺导出工具时可为 `[]`，并写入 `limitations`。 |
| `entities` | list | 结构化实体清单（见下）。 |
| `relations` | list | 结构化关系清单（见下）。 |
| `legend` | list | 图例项（见下），必须覆盖图中实际使用的关系类型与实体类型。 |
| `data_mappings` | list | 数据编码映射（见下）。 |
| `sources` | list | 聚合来源定位（可选为空），用于元素→材料位置/版本的总索引。 |
| `uncertainty` | list | 歧义与缺口（见下）。 |
| `pending_human` | list | 需人工确认的项（见下）。 |
| `change_log` | list | 增量修改记录。首条必须为 3 分钟内的 `action="skeleton"` 骨架落盘，此后每次修改追加一条。 |
| `limitations` | list | `status != success` 时**必填**非空；success 时可为空。 |
| `errors` | list | 运行期错误（可为空）。 |
| `degradations` | list | 降级记录（可为空）。 |
| `runtime` | object | `{started_at, finished_at, duration_sec, model_requests_used, steps}`，运行元数据（数值未知写 `null`）。 |

## 子对象结构

### entities 每项
```json
{
  "id": "e1",
  "label": "EGFR",
  "type": "gene|protein|complex|family|cell_type|small_molecule|other",
  "compartment": "extracellular|membrane|cytosol|nucleus|...",
  "transmembrane": false,
  "display": {"italic": false, "prefix": "", "suffix": ""},
  "aliases": ["HER1"],
  "sources": [{"locator": "materials/paper.pdf#p3", "quote": "逐字引文", "version": ""}]
}
```
- `id` 全文档唯一；`type` 决定形状与图例；`display.italic` 用于基因斜体/蛋白正体；`sources[].quote` 必须是材料中的**逐字片段**。

### relations 每项
```json
{
  "id": "r1",
  "source": "e1",
  "target": "e2",
  "type": "activation|inhibition|binding|modification|transport|conversion|recruitment|unknown",
  "direction": "forward|reverse|bidirectional",
  "modifier": "phosphorylation",
  "evidence": "reported|inferred|uncertain",
  "sources": [{"locator": "...", "quote": "..."}]
}
```
- `source`/`target` 必须指向 `entities` 中已存在的 `id`。
- `type` 决定箭头样式；`modifier` 用于修饰关系上的标注（P/Ub/Ac/Me…）。

### legend 每项
```json
{"key": "activation", "visual": "solid arrow", "meaning": "激活", "data_mapping": null, "sources": []}
```
- `key` 与关系/实体类型的键一致（`check` 会做覆盖校验）。

### data_mappings 每项
```json
{"encoding": "node_fill|node_size|edge_width", "channel": "表达量",
 "scale": "high=#C62828,low=#B0BEC5", "values": {"e3": "high", "e1": "low"},
 "sources": [{"locator": "...", "quote": "..."}]}
```
- `scale`：分类用 `cat=color`，数值用 `min=…,max=…`。

### uncertainty / pending_human / change_log 每项
```json
{"item": "r4", "kind": "direction", "detail": "反馈方向按上下文推断", "resolution": "inferred"}
{"item": "e2", "reason": "同名基因与蛋白歧义", "suggested_action": "按上下文证据判定"}
{"time": "2026-11-22T08:00:00Z", "action": "build", "detail": "渲染 SVG"}
```

## 一致性不变量（check 脚本强制）

1. 18 个顶层字段齐全；`case_id` 非空；`status` 取值合法。
2. `editable_source_files` 非空、路径存在、SVG 可被 XML 解析且含 ≥2 个 `layer-*` 命名图层。
3. `export_artifacts` 中列出的每个文件真实存在且通过魔数校验（PDF 以 `%PDF-` 开头，PNG 以 `\x89PNG` 开头）。
4. `relations.source/target` 均可解析到实体 id。
5. `legend` 覆盖 `relations` 实际使用的关系类型与 `entities` 实际使用的实体类型。
6. `change_log` 非空且首条为 skeleton；`status != success` 时 `limitations` 非空。
7. 关键实体/关系应有 `sources`，且 `quote` 非空（空引文→警告，非阻断）。
