# output_contract 字段定义（默认契约）

当 `/input/case.json` 未提供 `output_contract` 时，使用以下默认契约。若提供了 `output_contract`，**原样保留其全部字段**（含未知键），缺值补 `null`/`[]`，不得改名、不得删键。

## 顶层必含字段

| 字段 | 类型 | 含义 |
|-|-|-|
| `case_id` | string | 案例标识，优先 `case.json.id`，其次 `armiId` |
| `status` | string | `complete` / `partial` / `failed`，须诚实标注 |
| `sources` | array | 实际读取的来源清单，每项 `{path, name, type, read}` |
| `uncertainty` | array | 无法判断、材料缺失、证据不足等逐条说明 |
| `pending_human` | array | 需人工确认/补齐的事项 |
| `change_log` | array | 时间戳 + 动作 + 影响范围的增量记录 |
| `errors` | array | 运行中捕获的错误 |
| `degradations` | array | 降级处理记录（缺图/缺字体/导出失败等） |
| `runtime` | object | 起止时间与预算记录 |

## 默认契约内容字段

### report_goal（报告目标）
可检查的目标，非空话。至少含：`objective`（本次要达成什么）、`checkable_outcomes`（可核验结果，如“让评委认可方法新颖性”）、`time_budget`（分钟）、`slide_count`（页数）。

### audience（听众）
`profile`（身份）、`prior_knowledge`（先验）、`concerns`（关注点）、`expected_objections`（可能质疑）。听众决定背景铺垫比例、术语解释范围与证据展示深度。

### narrative（叙事顺序）
`spine`（问题—方法—结果—结论主骨架）、`ordering_rationale`（顺序与取舍理由，说明为什么不是按论文目录搬运）、`tradeoffs`（不上页的内容与原因）。

### pages（页级结构，数组）
每页对象：

- `page_index`：页码
- `title`：页面标题
- `single_claim`：**唯一**主张（一句可讲的结论）
- `evidence_strength`：`high` / `medium` / `low`
- `figures`：图表引用（图/表编号或路径）
- `speaker_notes`：讲稿备注（与页面结论对应，不与页面文字重复）
- `source_refs`：来源定位数组
- `quote`：源材料逐字引用（可选但推荐，供 evidence_map 复核）

### editable_deck（可编辑演示文件）
`path`（绝对路径）、`format`（pptx/html/odp）、`template`（所用母版模板）、`fonts`（字体清单）。

### evidence_map（页级证据映射，数组）
每项：`page_index`、`claim`、`locator`（章节/图/表/页码）、`quote`（源材料逐字片段）、`evidence_strength`。**quote 必须逐字来自源材料，不得改写事实。**

### qa_prep（问答准备，数组）
每项：`question_type`（mechanism/data/control/limitation）、`anticipated_question`、`answer_point`（口径一致的回答要点）、`referable_page`（可引用页面）、`backup_page`（备用页，可为 null）。

### rendering（渲染与版本记录）
`source_files`、`exports`（导出件路径）、`fonts`、`template`、`modifications`（修改记录）。完成标准：可打开、播放、编辑并复核。

## 填充示例（示意，字段值为占位，非正式案例内容）

`pages[0]` 示意：

```json
{
  "page_index": 1,
  "title": "研究问题",
  "single_claim": "现有方法在<具体条件>下存在<具体瓶颈>",
  "evidence_strength": "high",
  "figures": ["Fig.1"],
  "speaker_notes": "先给出差距再引出本工作；<一句话讲清原因>",
  "source_refs": ["sec.1", "Fig.1"],
  "quote": "<源材料逐字片段>"
}
```

`evidence_map[0]` 示意（quote 必须逐字来自源材料）：

```json
{
  "page_index": 1,
  "claim": "现有方法在<具体条件>下存在<具体瓶颈>",
  "locator": "sec.1 / Fig.1",
  "quote": "<源材料逐字片段>",
  "evidence_strength": "high"
}
```

`qa_prep[0]` 示意：

```json
{
  "question_type": "limitation",
  "anticipated_question": "<针对局限的追问>",
  "answer_point": "<口径一致的回答要点>",
  "referable_page": 6,
  "backup_page": 7
}
```

以上均为接口示意，不含任何正式案例的科学结论。

