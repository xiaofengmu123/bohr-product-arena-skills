# result.json 输出契约

本文件定义 `slides-robust-adapt` 产出的 `/app/output/result.json` 全字段。
所有字段在骨架阶段即已写好（空值 `null`/`[]`），后续只做增量填充；缺值不得改名、
不删未知键。若 `case.json` 自带 `output_contract`，以其为准并补齐下述字段。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识；优先取 `case.json.case_id`，其次 `id`，再回退文件名/task.md 提取 |
| `status` | string | `ok / partial / failed / insufficient_evidence / input_error`（骨架期为 `in_progress`） |
| `started_at` / `finished_at` | string\|null | ISO-8601 时间戳 |
| `presentation` | object | 可编辑演示文件（见下） |
| `evidence_map` | object | 页级证据映射（见下） |
| `render_record` | object | 渲染与版本记录（见下） |
| `sources` | array | 源文件清单，每项 `{path, role}` |
| `uncertainty` | array | 逐条 `{code, page?, detail}`，未满足项如实记录 |
| `pending_human` | array | 需人工复核/决策的事项，每项 `{item, detail}` |
| `change_log` | array | 每次落盘/合并/收口的 `{at, event, detail?, status?}` |
| `errors` | array | 致命错误（坏 JSON、缺文件等） |
| `degradations` | array | 降级记录（超大文件跳过、output_contract 非对象等） |
| `runtime` | object | `{requests, seconds, rework_pages}` 实际消耗，供公平对照 |

## presentation（可编辑演示文件）

| 字段 | 类型 | 说明 |
|---|---|---|
| `pptx_path` | string\|null | 可编辑 pptx 绝对/相对路径 |
| `pdf_path` | string\|null | 真实渲染 PDF 路径（必须） |
| `png_paths` | array | 真实渲染 PNG 预览路径列表 |
| `slide_count` | int\|null | 页数 |
| `page_plan` | array | 每页 `{index, title, claim, layout, notes, refs}` |

`page_plan[].refs` 指向源材料行号/图号；`notes` 即讲稿备注，降低人工核对量。

## evidence_map（页级证据映射）

每页 `{page, claim, refs[], evidence_status}`；`refs` 用 `locator + quote` 双元组，
`quote` 必须是源材料逐字片段。未定位的引用写入 `unresolved`。

- `evidence_status`：`located / inferred / not_reported / source_conflict`。
- 冲突信息不隐藏：同一指标两个来源不一致时两者都记，标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## render_record（渲染与版本记录）

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_files` | array | 论文/材料文件清单 |
| `template_file` | string\|null | 模板文件路径 |
| `layouts_used` | array | 每页用到的 layout，含替代关系（缺 layout 时显式命名） |
| `fonts` | array | 主题字体与回退字体记录 |
| `exports` | array | 导出件清单与导出设置 |
| `integrity` | object | `validate_deck.py` 的确定性检查结果 |
| `media` | array | 图表文件与尺寸，等比缩放规则记录 |

## 状态语义（诚实 > 完整）

- `ok`：模板真实套用、页内一致、导出件可复核。
- `partial`：主产物已生成，但有缺口（缺某 layout、某图失效等），缺口记入 `uncertainty`。
- `insufficient_evidence`：材料不足，无法生成可核对的演示（空输入）。
- `input_error`：case.json 不可解析等输入错误。
- `failed`：无法产出可用主产物（写盘失败、导出失败等）。

任何状态都不伪造运行分数、软件状态或科学数据。
