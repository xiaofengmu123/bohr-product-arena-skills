# 输出契约（output_contract）与 result.json 规范

本文件是 `result.json` 的唯一字段规范来源（single source of truth）。Skill 运行时以
`/input/case.json` 中携带的 `output_contract` 为准；当案例未提供时，以下方 **canonical
schema** 为准。任何情况下都必须逐字段回填，不得改名、不得删除未知键、不得用猜测值填充。

## 1. 对齐规则（本 Skill 的核心设计取舍）

1. **键级一一对应**：`result.json` 顶层键 = `case_id` + `status` + 案例 `output_contract`
   的全部键（按原始顺序）+ canonical 中未出现过的补充键。未知键一律保留，缺值给 `null`
   （列表型给 `[]`），从不因为"不知道值"而删键。
2. **主产物路径不写死**：可编辑源文件、导出件路径统一相对于 `/app/output/` 写，避免跨
   环境失效。
3. **change_log 强制存在且递增**：任何一次对 `result.json` 的实质改写（骨架落盘、填充
   事实、重绘、导出、定稿）都必须追加一条 `change_log` 记录，`version` 单调递增，记录
   `time_utc`、`what`、`why`。这是"结果可复核"与"重复运行一致"的落地证据。
4. **证据溯源**：`entities` / `relations` / `data_mapping` / `legend` 中每个条目都带
   `source`（`locator` + 逐字 `quote`），并同时在顶层 `sources` 登记来源文件。没有逐字
   引用的数值或图元不得上图。
5. **诚实降级**：缺值、歧义、渲染失败、字体替换全部进入 `uncertainty` / `limitations` /
   `errors` / `degradations`，`status` 相应设为 `partial` 或 `failed`；禁止用估算值伪装
   成真实数据。

## 2. canonical schema（字段一览）

顶层必填：`case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`change_log`。

| 字段 | 类型 | 含义 | 缺失时 |
|-|-|-|-|
| `case_id` | string | 案例标识，取自输入 | 若不可得，用输入文件名，同时进 `errors` |
| `status` | enum | `completed \| partial \| failed`，三选一 | 骨架阶段为 `partial` |
| `graphical_abstract` | object | 四要素：`question` / `method` / `results` / `conclusion`，各为 string | 逐键 `null` |
| `editable_source` | object | `path`（相对 `/app/output/`）、`format`（`svg`）、`layers`（string 数组）、`text_editable`（bool） | `path=null`，`layers=[]`，`text_editable=null` |
| `exports` | array | 每项 `{path, format, renderer, status, width_px, notes}`；`status` 为 `rendered \| preview_not_rendered \| failed` | `[]` |
| `entities` | array | 每项 `{id, label, kind, source:{locator, quote}}`，可含 `x/y/w/h` | `[]` |
| `relations` | array | 每项 `{id, from, to, direction, kind, source:{locator, quote}}`；`direction` 为 `forward \| backward \| undirected` | `[]` |
| `data_mapping` | array | 每项 `{id, visual_channel, label, value, unit, source:{locator, quote}}`；`visual_channel` 为 `color \| length \| area \| luminance \| position \| size` | `[]` |
| `legend` | array | 每项 `{id, label, swatch, source:{locator, quote}}` | `[]` |
| `sources` | array | 每项 `{id, path, locator, quote, note}`；`path` 相对 `/input/` | `[]` |
| `uncertainty` | array | 每项 `{id, item, detail}`；记录歧义、方向不确定、单位缺失 | `[]` |
| `limitations` | array | 每项 `{id, item, detail}`；记录渲染器缺失、字体替换、画布/字号问题 | `[]` |
| `pending_human` | array | 每项 `{id, item}`；需要人工确认才能定稿的事项 | `[]` |
| `errors` | array | 每项 `{id, item, detail}`；读取/解析/写盘错误 | `[]` |
| `degradations` | array | 每项 `{id, item, detail}`；非致命降级（坏编码、超限跳过等） | `[]` |
| `runtime` | object | `{seconds, model_requests}`；由 Agent 尽力回填，未知给 `null` | 逐键 `null` |
| `change_log` | array | 每项 `{version, time_utc, what, why}`；`version` 从 1 单调递增 | 至少含第 1 条"骨架落盘" |

## 3. 一个字段的规范形态（示例，非正式答案）

```json
"relations": [
  {
    "id": "rel_1",
    "from": "ent_tf",
    "to": "ent_gene",
    "direction": "forward",
    "kind": "activation",
    "source": {
      "locator": "materials/paper.pdf#page=3",
      "quote": "TF activates transcription of the target gene."
    }
  }
]
```

`quote` 必须是材料中的逐字片段；`locator` 优先 `文件路径#page=N` 或 `文件路径#段落`。
找不到逐字依据时，把该条移入 `uncertainty`，不要在图面上给出无来源的箭头方向或数值。

## 4. 与评分维度的对应

- 结果完整性 / 可核验性 → 键级对齐 + `sources`/`change_log`/证据映射齐全；
- 结果正确性 → 实体、关系方向、数据通道、单位逐条溯源；
- 可靠体验（异常处理）→ `errors`/`degradations`/`uncertainty`/`limitations` 完整；
- 生态贡献 → 本规范可作为他人复用的模板，`scripts/extract_case.py` 会机械地保证对齐。
