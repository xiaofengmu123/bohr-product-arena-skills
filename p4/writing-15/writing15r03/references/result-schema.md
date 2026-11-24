# result.json 契约与证据/版本约定

本文件定义 `/app/output/result.json` 的规范结构，与 `SKILL.md` §3 一致。`scripts/main.py` 在 `init` 阶段即写入合法骨架，后续各阶段只做增量覆盖；若 `/input/case.json` 自带 `output_contract`，其全部键原样回填并保留未知键。

## 顶层键

| 键 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `case_id` | string | 是 | 输入案例标识；优先取 case.json 的 `case_id`/`id`，兜底用输入目录名并标注 |
| `status` | string | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `deliverable` | object | 是 | 目标产物（可编辑稿件/改稿/引用结果/结构化产物） |
| `fact_source_map` | list | 是 | 事实与来源映射，逐条可回溯 |
| `version_boundary` | object | 是 | 版本与边界：修改差异、人工确认、材料缺口、异常 |
| `sources` | list | 是 | 源文件/材料清单 |
| `uncertainty` | object | 是 | 不确定性、未证实断言、冲突 |
| `change_log` | list | 是 | 逐条修改记录（before/after/reason） |
| `errors` | list | 建议 | 运行错误 |
| `degradations` | list | 建议 | 降级处理（坏 JSON、超限、缺软件等） |
| `runtime` | object | 建议 | `{started, finished, elapsed_s}` |
| `output_contract` | object | 建议 | 原始契约回显，便于复核 |

## 三个写作交付字段

### deliverable（目标产物）
```json
{
  "path": "deliverables/draft.md",
  "format": "markdown",
  "title": "……",
  "description": "……",
  "completion": "draft|revised|checked",
  "preview_path": "deliverables/draft.pdf"
}
```
`path` 相对 `/app/output`；Office/幻灯片/图需同时给真实渲染的 PDF/PNG `preview_path`。

### fact_source_map（事实与来源映射）
每项 `{id, type, content, locator, quote, status, note}`：
- `type` ∈ `number | term | claim | citation | figure | table`
- `locator`：材料定位（如 `materials/paper.md#sec2¶3`）或 `citation:…`
- `quote`：来源中的逐字片段（必须是原文，不得改写）
- `status` ∈ `mapped | missing_source | conflict | pending_human`

冲突不隐藏：同一指标两个来源不一致时两条都记录并标 `conflict`，写入 `uncertainty.conflicts`。

### version_boundary（版本与边界）
```json
{
  "diff_summary": "本次改动范围与数量概述",
  "pending_human": ["需作者确认：……"],
  "material_gaps": ["缺……材料，无法判断……"],
  "exceptions": ["PDF 无法解析……"]
}
```
逐条改动的 `before/after/reason` 放顶层 `change_log`，保证“改得动、查得清”。

## status 判定

- `ok`：主产物齐备、证据逐条 `mapped`、编号/交叉引用一致、无 `pending_human`。
- `partial`：有可用产物但存在 `missing_source`/`conflict`/缺口/截断，或未完成全部章节。
- `insufficient_evidence`：材料为空或不足以支撑写作，未编造内容。
- `input_error`：case.json/task.md 不可解析且无法恢复。

判定证据不足时必须诚实，不得用示例填充冒充正式产物。
