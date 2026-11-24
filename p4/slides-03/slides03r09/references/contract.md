# result.json 字段定义

## 顶层字段

| 字段 | 类型 | 含义 |
|-|-|-|
| case_id | string | 案例标识（优先 `case.json.id`，其次 `armiId`） |
| status | string | `complete` / `partial` / `failed` / `insufficient_evidence` / `input_error` |
| sources | array | 实际读取的来源清单：`path`、`type`、`read` |
| uncertainty | array | 无法判断、材料缺失、证据不足等逐条说明 |
| change_log | array | `ts`、`action`、`scope` 的增量记录 |

其余字段与 `case.json.output_contract` 一一对应；无 `output_contract` 时使用默认字段：
`report_goal`、`audience`、`narrative`、`pages`、`editable_deck`、`evidence_map`、`qa_prep`、`rendering`。

## pages 元素

- `page_index`：页码；
- `title`：页标题；
- `single_claim`：本页唯一主张（一句话可讲结论）；
- `evidence_strength`：`high` / `medium` / `low`；
- `figures`：图表引用；
- `speaker_notes`：讲稿备注（与页面结论对应，不逐字重复）；
- `source_refs`：数组，每条含 `locator` + 逐字 `quote`。

## evidence_map 元素

- `claim`：被支撑的论断；
- `locator`：来源定位，形如 `materials/paper.md#p3` 或 `file:page`，须能指回源材料段落；
- `quote`：源材料逐字片段；
- `strength`：证据强度（high / medium / low）。

## 硬性约束

1. `output_contract` 的全部字段须原样保留并回填，不改名、不删键；缺值给 `null` / `[]`。
2. `quote` 必须是源材料的逐字片段，不得改写事实。
3. 未产出的字段留空或显式 `null`，不得编造数据、引用或来源。
4. 冲突来源不隐藏：同一指标两处不一致时，两处都记录并标注 `source_conflict`。
