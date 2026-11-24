# 输出契约与证据格式（canonical）

当 `/input/case.json` 未提供 `output_contract` 时，`result.json` 采用以下规范字段；若题目提供了 `output_contract`，则必须**原样回填其全部键**（缺值给 `null`/`[]`），再叠加本文的通用字段。字段名不可改名、不可删除未知键。

## 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 来自 case.json 的 `id`/`case_id`，缺失时用输入文件名指纹 |
| `status` | string | `completed` / `partial` / `failed`（见 degradation_rules.md） |
| `graphical_abstract` | object | `{question, method, results, conclusion}`，四要素各为短句；无依据的要素写 `null` |
| `editable_source` | object | `{path, format:"svg", layers:[...], text_editable:true}`，path 相对 `/app/output` |
| `exports` | array | 每项 `{path, format, renderer, status:"rendered"|"preview_not_rendered"}` |
| `entities` | array | 图面实体，每条带 `evidence` |
| `relations` | array | 图面关系，每条带 `evidence`，`directed:true|false` |
| `data_mapping` | array | 视觉通道→数据映射，每条带 `evidence` |
| `legend` | array | 图例项，每条带 `evidence` 或标 `pending_human` |
| `sources` | array | 材料文件清单 + 逐字引用片段 |
| `uncertainty` | array | 每项 `{item, reason, impact}` |
| `pending_human` | array | 需人工确认项 `{item, question, current_value:null}` |
| `limitations` | array | 明确限制 `{kind, detail}`（缺数据/渲染失败/字体替换/PDF 未解析…） |
| `change_log` | array | 每项 `{step, note, at}`，每次改写追加 |
| `runtime` | object | `{started_at, finished_at, seconds, requests_used, renderer}`，脚本自动填 |

## 证据对象（每个关键图元必须带）

```json
{
  "id": "ent-1",
  "label": "…",
  "kind": "molecule|cell|tissue|material|device|sample|condition|process",
  "evidence": {
    "locator": "materials/paper.md#L12",
    "quote": "逐字片段，不转写不缩写"
  },
  "confidence": "high|medium|low",
  "source": "materials/paper.md"
}
```

- `locator`：`文件路径#L行号` 或 `文件路径#p页码`（PDF 经 pdftotext 后按行，或按页）。
- `quote`：必须能从材料中逐字找到；找不到则该项进入 `pending_human` 或删除。
- 自动抽取默认 `confidence=low`；人工核对原文后可升 `high` 并保留 locator。
- 冲突：同一指标两来源不一致 → 都记录，标 `source_conflict`，图取其一并写入 `uncertainty`。

## 分层 SVG 约定

`fig_<case_id>.svg` 含命名组：`layer-background`、`layer-panels`、`layer-entities`、`layer-relations`、`layer-data`、`layer-labels`、`layer-legend`。文字一律 `<text>`（不得 text-as-path）；图元带 `data-source` 与 `data-evidence`（指回 evidence id）。画布默认 1400×1000，约束来自 case 的 layout 字段时以约束为准。

## 可复用图层骨架（他人可直接套用，**示例，非正式答案**）

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1000"
     viewBox="0 0 1400 1000" font-family="DejaVu Sans, Arial, sans-serif">
  <g id="layer-background">…</g>
  <g id="layer-panels">…</g>
  <g id="layer-entities">
    <rect x="…" y="…" width="…" height="…" data-source="materials/paper.md#L12"
          data-evidence="ent-1"/>
  </g>
  <g id="layer-relations">
    <line x1="…" y1="…" x2="…" y2="…" marker-end="url(#arrowhead)"
          data-source="materials/paper.md#L12" data-evidence="rel-1"/>
  </g>
  <g id="layer-data">…</g>
  <g id="layer-labels"><text>…</text></g>
  <g id="layer-legend">…</g>
</svg>
```

图层顺序即阅读层级：背景 → 面板 → 实体 → 关系 → 数据 → 标注 → 图例。`data-source` 存 locator，`data-evidence` 存 `evidence_bank.json` 中的条目 id，二者共同支撑逐项复核。

## 最小调用示例（**示例，非正式答案**）

输入 `/input/` 下放 `case.json`（含 `id` 与 `output_contract`）与 `task.md`，材料在 `/input/materials/`。调用：

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
```

脚本立即写 `/app/output/result.json`（status=partial）与 `/app/work/evidence_bank.json`、`/app/work/figure_spec.json`。随后 agent 在 `figure_spec.json` 中核对/补齐证据（locator+quote），再：

```bash
python3 -B scripts/build_svg.py --work /app/work --output /app/output
python3 -B scripts/selftest.py
```

示意输出（结构，**非正式案例结果**）：`result.json` 含 `case_id`、`graphical_abstract` 四要素、`entities/relations/data_mapping/legend`（各带 evidence）、`editable_source`（指向 `fig_<case_id>.svg`）、`exports`（PNG/PDF 及 `rendered`/`preview_not_rendered`）、`sources`、`uncertainty`、`pending_human`、`limitations`、`change_log`。
