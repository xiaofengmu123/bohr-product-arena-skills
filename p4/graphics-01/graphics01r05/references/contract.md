# result.json 输出契约（单源契约）

本文件与 `scripts/scaffold.py` 中的 `RESULT_CONTRACT` 是**同一份契约**。改字段时必须两处同步，并以 `scripts/selftest.py` 验证一致性。运行时产物固定写 `/app/output/result.json`。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 默认 | 语义 |
|---|---|---|---|---|
| `case_id` | string | 是 | `unknown` | 从 `case.json` 提取的案例标识 |
| `status` | enum | 是 | `partial` | `complete` / `partial` / `failed` / `insufficient_evidence` / `input_error` |
| `editable_source_files` | list | 否 | `[]` | 可编辑源文件（分层 SVG、数据文件、Office 文档） |
| `export_files` | list | 否 | `[]` | 渲染导出件（PNG/PDF），含尺寸/dpi/字体/色彩检查 |
| `provenance` | list | 否 | `[]` | 每个图面元素 → 来源定位 + 逐字引用 + 映射 + 版本 |
| `sources` | list | 否 | `[]` | 实际读过的材料（路径 + locator + 用途） |
| `uncertainty` | list | 否 | `[]` | 未决/推断项（原因 + 影响 + 缓解） |
| `pending_human` | list | 否 | `[]` | 需人工确认的决策或复核项 |
| `change_log` | list | 否 | `[]` | 按时间排序的动作日志 |
| `degradations` | list | 否 | `[]` | 已应用的降级（OCR、字体替换、跳过的文件） |
| `errors` | list | 否 | `[]` | 可恢复错误；失败时不得为空 |
| `runtime` | object | 否 | `{}` | 运行元信息；真实预算/分数由平台记录，不得编造 |

## 2. 回填规则（硬约束）

1. `result.json` 必须同时含 `case_id`、题目 `output_contract` 的全部字段，并追加 `status/sources/uncertainty/change_log`（以及 `pending_human/degradations/errors/runtime`）。
2. `case.json` 若声明了原始 `output_contract`，其**未知键一律原样回填**为 `null`/`[]`，不改名、不删除、不静默省略。
3. 任意数组为空时必须说明原因：写入对应条目的 `note`，或 `uncertainty`/`degradations`/`errors`，不得留空装完成。
4. `status` 诚实：材料不足用 `insufficient_evidence`；无法读取输入用 `input_error`；部分交付用 `partial` 并标注 limits；绝不冒充 `complete`。

## 3. 各数组条目建议字段（`ITEM_HINTS`）

- `editable_source_files`：`path, kind, layers, editable_text, note`
- `export_files`：`path, format, width_px, height_px, dpi, color_space, font_check, note`
- `provenance`：`element, source_ref, quote, mapping, version, confirmed`
- `sources`：`ref, path, locator, used_for`
- `uncertainty`：`item, reason, impact, mitigation`
- `pending_human`：`item, reason, suggested_action`
- `change_log`：`time, action, detail`
- `degradations`：`item, reason, fallback`
- `errors`：`item, reason, handling`

## 4. 证据溯源要求

- 每个关键图面元素、数值、单位、关系方向，必须在 `provenance` 中给 **locator**（页码/段落/表行）与 **逐字 `quote`**；无法逐字引用就标 `not_reported` 或 `pending_human`，不得凭空补造基因名/浓度/时间点/单位。
- 同一指标出现两个来源不一致时，两条都记录并标 `source_conflict`（写入 `uncertainty`）。
- `sources` 只列实际读过、真实存在的材料；PDF 经 OCR 识别的必须在 `sources`/`degradations` 标注「OCR 识别，需人工复核」。

## 5. 状态与降级对照

| 情形 | `status` | 必填记录 |
|---|---|---|
| 全部交付且自检通过 | `complete` | `change_log` 有 finalized 条目 |
| 部分交付/有 limits | `partial` | `uncertainty` 或 `change_log` 写明 limits |
| 材料不足、无法判断 | `insufficient_evidence` | `pending_human`/`uncertainty` 列出缺口 |
| 输入损坏/不可读 | `input_error` | `errors` 记录原因 |
| 导出失败 | `partial`（保留源文件） | `export_files` 记 failed 与原因，不伪造 PNG/PDF |

## 6. 一致性保证

`scaffold.py` 按 `RESULT_CONTRACT` 生成骨架（每个字段显式默认值，含 echo 未知键）；`finalize.py` 合并 `work/*.json` 并做类型/枚举/必填校验。二者都以本契约为准，因此 `result.json` 不会因疏忽缺字段。
