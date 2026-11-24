# result.json 输出契约（字段说明）

本文件是 `scripts/contract.py` 所镜像的字段规范。契约引擎把 `case.json` 中真实的
`output_contract`（若有）作为唯一事实来源，逐字段镜像到 `result.json`；若题目未显式
给出 `output_contract`，则使用下方默认契约。**未知字段不得改名、不得删除**，缺值填
`null` 或 `[]`，不允许编造数值或软件状态。

## 0. 信封字段（每份结果必含）

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识，取自 `case.json.case_id`（或 `id`/`caseId`/`challenge_id`/`armiId`），缺省为输入目录名 |
| `status` | string | `ok / partial / insufficient_evidence / input_error / failed / in_progress` 之一，必须诚实 |
| `sources` | list | 源文件清单，每项 `{path, kind, note}`；`path` 必填 |
| `uncertainty` | list | 未满足/无法判断项，每项 `{code, page?, detail}`；`code` 与 `detail` 必填 |
| `change_log` | list | 追加式修改记录，每项 `{ts, code, detail, page?}`；不得改写历史条目 |
| `pending_human` | list | 需要人工核验或补料的事项，可为空 |
| `errors` | list | 运行期错误，空目录/坏 JSON/写盘失败等都应记录 |
| `degradations` | list | 降级项（缺字体、缺版式、导出失败等），每项说明替代方案 |
| `runtime` | object | `{started_at, finished_at, requests_used}` 等实际消耗记录 |

## 1. presentation（可编辑演示文件）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `pptx_path` | string | 是 | 可打开、可继续编辑的 `.pptx` 绝对路径 |
| `pdf_path` | string | 否 | 真实渲染的 PDF 预览；导出失败时保留 `null` 并记 `uncertainty` |
| `png_paths` | list | 否 | 逐页 PNG 预览路径 |
| `slide_count` | integer | 否 | 实际页数 |
| `page_plan` | list | 否 | 页级计划，每项见 §4 |

`page_plan` 项：`{index, title, claim, layout, notes, refs}`；`index` 与 `claim`
必填，`refs` 为证据定位列表。

## 2. evidence_map（页级证据映射）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `evidence_map` | list | 是 | 每页内容对应论文图/表/章节/数据 |

项：`{page, claim, refs[], evidence_status}`；`page` 与 `claim` 必填。`refs` 中每个
定位至少含 `locator`（源文件路径/行号/页码）与 `quote`（逐字片段）。证据状态取
`ok / partial / source_conflict / not_reported` 之一。

## 3. render_record（渲染与版本记录）

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_files` | list | 输入论文/材料/图表源文件 |
| `template_file` | string | 模板文件路径 |
| `layouts_used` | list | 实际使用的版式，含替代关系（如 `{wanted, used, reason}`） |
| `fonts` | list | 每项 `{role, name, available, fallback}`，缺失必须记录回退字体 |
| `exports` | list | 导出件记录，每项 `{kind, path, ok}` |
| `integrity` | object | 文件完整性/页数/母版继承检查结论 |
| `media` | list | 嵌入图表清单 |

## 4. change_log 语义

- 追加式（append-only）：历史条目不得删除或改写，只允许追加。
- 每条含 `ts`（UTC 时间戳）、`code`（如 `bootstrap / init / extract / plan /
  build / render / validate / finalize / degrade / input_error`）、`detail`。
- 骨架阶段就写入 `bootstrap` 条目；之后每个关键步骤各记一条，便于复跑逐页核对。

## 5. 状态判定

- `ok`：三项主产物齐全，契约必填字段全部非空，文件真实存在。
- `partial`：已落盘部分产物，但存在必填字段为空、导出失败、字体缺失等，已如实
  写入 `uncertainty`/`degradations`。
- `insufficient_evidence`：材料不足，无法定位证据，不编造结论。
- `input_error`：`case.json` 缺失或无法解析。
- `failed`：关键步骤失败且无可交付产物。

任何中途异常都应保留已落盘的 partial 结果，不因一步失败丢弃全局产物。
