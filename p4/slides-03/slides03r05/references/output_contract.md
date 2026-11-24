# output_contract 字段定义（契约对齐版）

本文件定义 `result.json` 的字段与子结构。字段名、类型与说明与 `scripts/core_impl.py` 中的契约保持一致；运行时若 `/input/case.json` 提供 `output_contract`，其未知键原样追加、缺值给 `null`/`[]`/`""`，不改名、不删除。

## 顶层字段顺序

`case_id` → 领域契约字段 → 元数据字段（`status` / `sources` / `uncertainty` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`）。

## 标识与领域契约字段

| 字段 | 类型 | 必需 | 说明 |
|-|-|-|-|
| `case_id` | string | 是 | 案例标识；优先 `case.json.id`，其次 `armiId`，再次 `case_id`。 |
| `report_goal` | object | 是 | 可检查目标：`objective`、`audience`、`duration_min`、`slide_count`、`template`。 |
| `audience` | object | 是 | 听众：`prior_knowledge`、`concerns`、`likely_questions`、`terminology_scope`。 |
| `narrative` | object | 是 | 叙事：`story_arc`、`sequence_rationale`、`omissions`（取舍理由，非目录搬运）。 |
| `pages` | array | 是 | 页级结构数组，元素见下表。 |
| `editable_deck` | object | 是 | `path`、`format`、`template`、`layout`。 |
| `evidence_map` | array | 是 | 页级证据映射，元素见下表。 |
| `qa_prep` | array | 是 | 问答预演，元素见下表。 |
| `rendering` | object | 是 | `source_files`、`exports`、`fonts`、`template`、`notes`。 |

## 元数据字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `status` | enum | `complete` / `partial` / `failed` / `insufficient_evidence` / `input_error`，如实标注。 |
| `sources` | array | 实际读取的来源清单：`path` / `type` / `read`。 |
| `uncertainty` | array | 无法判断、材料缺失、证据不足等逐条说明。 |
| `pending_human` | array | 需人工裁决或补料的项。 |
| `change_log` | array | 追加式增量记录，条目见下表。 |
| `errors` | array | 运行错误。 |
| `degradations` | array | 降级说明（缺图 / 字体 / 导出失败 / 超时等）。 |
| `runtime` | object | 运行信息（工具、耗时等），非评测分数。 |

## pages 元素

| 字段 | 必需 | 说明 |
|-|-|-|
| `page_index` | 是 | 页码（从 1 开始）。 |
| `title` | 是 | 页标题。 |
| `single_claim` | 是 | 本页唯一主张，一句话可讲。 |
| `evidence_strength` | 是 | `high` / `medium` / `low` / `n/a`，与证据匹配。 |
| `figures` | 是 | 图表引用数组。 |
| `speaker_notes` | 是 | 讲稿备注，与页面结论对应、不重复正文。 |
| `source_refs` | 是 | 来源引用数组。 |
| `locator` | 是 | 来源定位，如 `materials/paper.md#p3`。 |
| `quote` | 是 | 支撑本页主张的逐字片段。 |

## evidence_map 元素

| 字段 | 必需 | 说明 |
|-|-|-|
| `page_index` | 是 | 对应页码。 |
| `claim` | 是 | 本页主张（与 `pages[].single_claim` 一致）。 |
| `locator` | 是 | 来源定位。 |
| `quote` | 是 | 逐字 quote，未改写事实。 |
| `figure_table` | 否 | 图/表编号。 |
| `confidence` | 否 | `high` / `medium` / `low`。 |

## qa_prep 元素

| 字段 | 必需 | 说明 |
|-|-|-|
| `question` | 是 | 预判追问。 |
| `answer` | 是 | 口径一致的回答要点。 |
| `cites_page` | 是 | 可引用页码数组。 |
| `category` | 否 | `mechanism` / `data` / `control` / `limitation` 四类至少各一条。 |

## change_log 元素

| 字段 | 说明 |
|-|-|
| `ts` | ISO-8601 UTC 时间戳。 |
| `actor` | `script` / `agent`。 |
| `action` | `bootstrap` / `merge` / 其他动作。 |
| `scope` | 本次影响的字段数组。 |
| `note` | 取舍或修改说明。 |

## 契约对齐校验

`main.py validate` 逐项检查：领域契约字段与 result.json 逐一对齐、元数据字段齐全、`status` 在枚举内、每页有 `single_claim`、证据条目有 `locator` 与 `quote`、qa_prep 覆盖四类追问、change_log 非空。缺字段报 error，弱结构报 warning。
