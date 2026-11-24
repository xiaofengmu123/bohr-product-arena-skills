# result.json 输出契约

`/app/output/result.json` 是唯一的结构化结果入口。字段分为四组：标识、题目契约、证据与状态、运行记录。缺值一律给 `null`/`[]`，不改名、不删未知键；若 `case.json` 自带 `output_contract`，以其键为准并补齐下文字段。

## 1. 标识与状态

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 输入案例标识；缺输入时为 `null` |
| `status` | string | `in_progress` / `ok` / `partial` / `insufficient_evidence` / `input_error` / `failed` |

`status` 须诚实：材料不足给 `insufficient_evidence`，部分缺失给 `partial`，不得用 `ok` 掩盖缺口。

## 2. 题目契约字段（三件套）

### presentation（可编辑演示文件）
- `pptx_path` / `pdf_path`：可编辑源文件与渲染预览，缺产物时为 `null`。
- `png_paths`：逐页 PNG 预览数组，可空。
- `slide_count`：实际页数，未生成时为 `null`。
- `page_plan`：数组，每项 `{index, title, claim, layout, notes, refs[]}`；`refs` 指回源材料定位。

### evidence_map（页级证据映射）
- 数组，每页 `{page, claim, refs[], evidence_status}`。
- `refs` 每项 `{locator, quote}`：`locator` 形如 `paper.txt:12` 或 `paper.pdf#p3`；`quote` 为源材料**逐字**片段。
- `evidence_status`：`located`（全部 ref 有定位且 quote 命中）/ `partial`（有缺口）/ `unverified`。

### render_record（渲染与版本记录）
- `source_files`：源材料清点（路径/大小/sha256）。
- `template_file` / `layouts_used` / `fonts`：模板与真实继承关系、实际使用字体。
- `exports`：PDF/PNG 导出件清单（存在性、签名、sha256）。
- `integrity`：pptx 页数、母版/版式数、备注数、slide 尺寸。
- `media`：图片等比检查结果（`aspect_ok`）。
- `checks` / `human_review`：自动检查结果与只能人工核验的边界。

## 3. 证据与状态字段

- `sources`：数组，逐项 `{path, locator, quote, role}`，用于复核溯源。
- `uncertainty`：数组，逐项 `{code, page?, detail}`；缺版式/缺字体/图源失效/导出失败逐条写明。
- `pending_human`：数组，需人工决断的事项。
- `change_log`：数组，逐项 `{ts, event, detail}`，记录骨架→制作→校验→收口全过程。

## 4. 运行记录

- `runtime`：`{started_at, updated_at}`，ISO-8601 UTC 时间戳；实际耗时与请求数由平台记录，不得自造分数。

## 5. 合法状态示例（示意，非正式案例）

```json
{"case_id":"case-x","status":"partial",
 "presentation":{"pptx_path":null,"pdf_path":null,"png_paths":[],"slide_count":null,"page_plan":[]},
 "evidence_map":[],"render_record":{"fonts":[]},
 "sources":[],"uncertainty":[{"code":"missing_template","detail":"template not provided"}],
 "pending_human":[],"change_log":[]}
```
