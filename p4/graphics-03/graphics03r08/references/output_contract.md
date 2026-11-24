# result.json 输出契约

本文件定义 `/app/output/result.json` 的字段。缺失字段一律给 `null`/`[]`，不删除未知键。所有路径均为 `/app/output/` 下的相对路径（或绝对路径，二者择一但须实际存在）。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取自 `/input/case.json` 的 `case_id`/`id`；找不到则用输入文件名 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error`，必须如实 |
| `title` | string | 图稿标题（可空） |
| `task_note` | string | 对任务要求的一句话归纳（可空） |
| `editable_source_files` | array | 可编辑源文件清单，至少 1 个分层 SVG |
| `export_artifacts` | array | 导出件清单（PDF/PNG），含尺寸/分辨率/字体/色彩检查 |
| `entities` | array | 实体规格（与 spec.json 一致） |
| `relations` | array | 关系规格（与 spec.json 一致） |
| `legend` | array | 图例项（由 build 自动生成，保证图面自洽） |
| `data_mappings` | array | 数据编码映射（颜色/线宽/大小/形状 → 数据含义） |
| `sources` | array | 聚合来源（去重），每项含 locator + quote |
| `uncertainty` | array | 歧义实体、方向不明、无来源数值 |
| `pending_human` | array | 需要人工确认的条目 |
| `change_log` | array | 增量修改记录，首条必须为骨架落盘 |
| `rubric_alignment` | object | 评分维度 → 完成状态与落点（本 Skill 特色字段） |
| `limitations` | array | partial/insufficient 时必填的缺口说明 |
| `degradations` | array | 工具/字体/格式降级记录 |
| `errors` | array | 读取/校验/导出错误 |
| `runtime` | object | 运行元信息（版本、耗时、所用工具），不得编造 |

## 子结构

### entity（entities 的元素）

```json
{
  "id": "e1",
  "label": "EGFR",
  "kind": "gene | protein | complex | family | cell_type | compartment | other",
  "species": "human 或留空",
  "compartment": "extracellular | membrane | cytosol | organelle | nucleus | unknown",
  "transmembrane": false,
  "aliases": ["ERBB1"],
  "inferred": false,
  "sources": [{"locator": "materials/paper.md#L12", "quote": "逐字引用原文"}]
}
```

规则：`inferred:true` 表示候选（低置信），可无 quote；`inferred:false`（断言）必须有至少一条非空 quote，否则校验判阻断。

### relation（relations 的元素）

```json
{
  "id": "r1",
  "from": "e1",
  "to": "e2",
  "type": "activation | inhibition | binding | modification | transport | conversion",
  "direction": "forward | reverse | unknown",
  "modifier": "phosphorylation 或留空",
  "evidence": "stated | inferred | unknown",
  "inferred": false,
  "sources": [{"locator": "...", "quote": "..."}]
}
```

规则：`from`/`to` 必须指向 entities 中存在的 `id`；`type` 必须在枚举内。

### source / data_mapping / uncertainty / pending_human / change_log

- `source`：`{"id", "file", "locator", "quote", "kind"}`；`quote` 必须是材料中的逐字片段。
- `data_mapping`：`{"channel": "color|width|size|shape", "data": "表达量|显著性|...", "encoding": "具体编码规则", "sources": [...]}`。
- `uncertainty`：`{"id", "target", "issue", "resolution"}`。
- `pending_human`：`{"id", "issue", "recommendation"}`。
- `change_log`：`{"time", "step", "summary"}`，首条 `step` 为 `skeleton`。

### editable_source_files / export_artifacts 的元素

```json
{
  "path": "mechanism.svg",
  "format": "svg | pdf | png",
  "role": "primary_editable | rendered_preview | data",
  "layers": ["background", "compartments", "edges", "nodes", "labels", "legend"],
  "width_px": 1200,
  "height_px": 800,
  "dpi": 150,
  "renderer": "stdlib-vector | office-renderer | browser-automation",
  "font_check": "可替换字体栈；导出字体状态",
  "color_check": "色板与图例一致"
}
```

`export_artifacts` 只记录真实生成的文件；未生成的格式不写条目，缺口写入 `limitations`。
