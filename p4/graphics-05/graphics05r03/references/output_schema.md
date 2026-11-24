# result.json 输出模式与证据规则

`scripts/main.py` 各阶段会读写 `/app/output/result.json`。本文件是该结构的规范说明；`scripts/core_impl.py` 中的 `canonical_result()` 是同一结构的机器实现。二者不一致时以代码为准。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 从 case.json 的 `case_id`/`id`/`challenge_id` 派生，缺失时占位 `case-unknown` |
| `status` | string | `completed`（有实体且无 error 缺陷）\| `partial` \| `failed`（critical 缺陷或无 case.json） |
| `graphical_abstract` | object | `question` / `method` / `results` / `conclusion`，缺一项为 `null` |
| `editable_source` | object | `format`=svg，`path` 相对 `/app/output`，`layers` 为 layer-* 组名，`text_editable`=true |
| `exports` | array | 每项 `{format, path, renderer, status}`；path 相对 `/app/output`；status ∈ rendered \| render_failed \| preview_not_rendered |
| `entities` | array | 科学实体，每个至少含 `id`/`label`/`source`/`quote` |
| `relations` | array | 实体间关系，每个至少含 `id`/`from`/`to`/`direction`/`source`/`quote` |
| `data_mapping` | array | 核心数值到视觉通道的映射，每个含 `id`/`value`/`unit`/`encoding`/`source`/`quote` |
| `legend` | array | 图例项 `{id, label, color, target, source, quote}` |
| `sources` | array | 唯一 locator 汇总 `{id, locator, quote}` |
| `uncertainty` | array | 无法确定/缺值项 `{id, issue, note}` |
| `pending_human` | array | 需要人工确认的项 `{id, issue, note}` |
| `change_log` | array | 每次写盘 `{seq, step, action, reason}`；seq 递增，无时间戳 |
| `limitations` | array | 已知限制/降级说明 |
| `errors` | array | 输入解析等错误 |
| `degradations` | array | 编码、超大文件跳过等降级 |
| `determinism` | object | `schema_version`、`result_version`、`seed`="none"、`layout`、`fingerprint` |

此外，`case.json` 中 `output_contract` 的全部键**原样回填**：未知键保留、缺值给 `null`/`[]`，不重命名、不删除。字段顺序无关紧要，因为所有 JSON 都以 `sort_keys=True` 序列化。

## 2. 证据溯源规则（硬要求）

每个关键结论、数值、图元都要能指回材料的**逐字片段**：

- 定位符 locator 形式建议为 `materials/paper.md#p3` 或 `figures/fig1.pdf#p2`（文件 + 段落/页）。
- `quote` 必须是从材料逐字复制的一段；不能凭记忆改写数值或表述。
- 同一指标两个来源不一致时，两个都记录并在 `uncertainty` 标 `source_conflict`，不隐藏冲突。
- 追不到来源的数值写 `null` + `uncertainty`；不虚构 DOI、引用或数值。

`scripts/main.py validate` 自动做两项检查：① 每个 entity/relation/data 都有 `source`；② `source` 在 `evidence` 列表中登记。未满足者进 `uncertainty`，并在 `validation_report.json` 记为 `missing_source` 或 `source_not_in_evidence`。

## 3. figure_spec.json 模式（工作文件）

`extract` 生成模板，Agent 在阶段 2 填入科学内容：

```json
{
  "schema": "figure-spec-v1",
  "case_id": "...",
  "title": "...",
  "canvas": {"width": 1200, "height": 900, "unit": "px"},
  "panels": [
    {"id": "question", "label": "Question", "order": 1},
    {"id": "method", "label": "Method", "order": 2},
    {"id": "results", "label": "Results", "order": 3},
    {"id": "conclusion", "label": "Conclusion", "order": 4}
  ],
  "entities":   [{"id", "label", "kind", "panel", "source", "quote"}],
  "relations":  [{"id", "from", "to", "direction", "label", "source", "quote"}],
  "data":       [{"id", "label", "value", "unit", "encoding", "panel", "source", "quote"}],
  "constraints":[{"type", "label", "value", "source", "quote"}],
  "legend":     [{"id", "label", "color", "target", "source", "quote"}],
  "evidence":   [{"id", "locator", "quote"}]
}
```

- `direction` ∈ `->`（正向，带箭头）\| `<-` \| `<->` \| `--`（无向）。箭头必须与原文因果方向一致。
- `encoding` ∈ `bar-length` \| `color` \| `area` \| `brightness`；数值、单位与编码一一对应。
- 实体 `panel` 缺省 `method`；布局由 `compute_layout()` 确定性计算，Agent 不需要手摆坐标。

## 4. 确定性约定

- `result.json` 不含墙钟时间、进程号、随机数；运行时遥测在 `/app/work/run_meta.json`。
- 所有 path 相对 `/app/output`，因此同一输入在不同机器/挂载点下字节一致。
- `determinism.fingerprint` 仅覆盖科学内容（abstract/entities/relations/data_mapping/legend/sources），不含导出状态。
- `scripts/selftest.py` 验证同一输入两次运行 `result.json` 与 SVG 字节一致。

## 5. 状态与降级对应

| 情况 | status | 附加字段 |
|-|-|-|
| 有实体、无 error/critical 缺陷 | `completed` | 常规 |
| 无实体或存在 error 缺陷 | `partial` | `uncertainty`+`limitations` |
| critical 缺陷 / 无 case.json | `failed` | `limitations` 说明阻塞 |
| 缺渲染器 | 不变 | `exports[].status=preview_not_rendered` |
| 数值缺失 | 不变 | 字段 `null` + `uncertainty` |
