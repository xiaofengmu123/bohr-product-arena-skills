# output_contract 字段定义（默认契约）

`result.json` 顶层字段。若 `/input/case.json` 给出 `output_contract`，其全部字段原样保留并平铺到顶层；未给出时使用下表默认值。缺失值一律给 `null`/`[]`，不编造。

## 状态枚举

- `status`：`complete` / `partial` / `insufficient_evidence` / `input_error` / `failed`

## 证据强度与置信度

- `evidence_strength`：`high` / `medium` / `low`
- `confidence`：`established`（已证实）/ `indicated`（初步表明）/ `speculative`（推测）/ `not_reported`（未报告）

两者必须与证据匹配：结果对比用 high/established，机制推测用 low/speculative，材料未报用 low/not_reported。

## 顶层字段表

| 字段 | 类型 | 说明 |
|-|-|-|
| case_id | string | 案例标识，优先 case.json.id，其次 armiId |
| status | string | 诚实状态（见上） |
| output_contract | object | 契约快照，供复核 |
| report_goal | object | 目标/听众/时长/页数，可检查 |
| audience | object | 先验、关注点、可能质疑 |
| narrative | string/array | 页面序列与取舍理由 |
| pages | array | 页级结构（见下） |
| claims | array | 证据台账（见下） |
| editable_deck | object | 路径/格式/所用模板 |
| evidence_map | object | 页 → 来源定位 + 逐字 quote |
| qa_prep | object | 机制/数据/对照/局限四类 |
| rendering | object | 源文件/导出件/字体/模板/修改记录 |
| limitations | array | 缺图/低分辨率/公式丢失/字体/模板冲突/导出失败/超时 |
| sources | array | 实际读取来源（path/type/read/错误） |
| uncertainty | array | 无法判断/缺失/证据不足 |
| pending_human | array | 需人工决策的缺口 |
| errors | array | 运行错误 |
| degradations | array | 降级记录 |
| change_log | array | ts/action/scope/detail |

## pages 条目

- `page_index`：页码
- `title`：页面标题
- `single_claim`：唯一主张（一句话）
- `evidence_strength`：high/medium/low
- `confidence`：established/indicated/speculative/not_reported
- `figures`：图表引用（与源材料一致）
- `speaker_notes`：讲稿备注（不与 single_claim 逐字重复）
- `source_refs`：来源定位（图/表/章节/页码）

## claims 条目

- `claim`：主张（一句话）
- `locator`：来源定位（图/表/章节/页码/文件名）
- `quote`：逐字原文，未改写
- `status`：`verified` / `pending` / `conflict`
