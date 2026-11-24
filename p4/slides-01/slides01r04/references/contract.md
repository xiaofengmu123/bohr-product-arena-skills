# 输出契约与字段要点（slides-01）

本文是 `scripts/main.py` 回填 `result.json` 时的字段规则速查。权威字段名以运行时
`/input/case.json` 的 `output_contract` 为准；本文只说明缺省约定。

## 1. 根字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 从 case.json 的 `case_id` 或 `id` 取；缺失为 `unknown-case` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error` |
| `editable_source` | object | `{path, format, slides}`，指向真实 pptx（无则 SVG 说明） |
| `export_artifact` | object | `{pdf:{path,pages}}`，指向真实预览件 |
| `content_map` | array | 每页一项：`{slide,title,claim,role,evidence[],notes,evidence_status}` |
| `compliance_checks` | array | 自检条目结果 |
| `exceptions` | array | 无法完成的契约项及原因 |

## 2. 统一信封（无论 contract 是否声明，最终都写入）

- `sources`：材料清单（`{path,kind,size,used}`）
- `uncertainty`：`{about,reason,evidence_status,mitigation}`
- `pending_human`：需要人工确认的事项
- `limitations`：材料/软件限制（缺什么、影响哪页、如何补救）
- `errors`：硬失败
- `degradations`：软降级（如“PDF 页数无法统计”“字体缺失”）
- `change_log`：修改记录（`{at,what,why}`）；contract 若要求 `changes`，回填同一内容
- `runtime`：`{started,finished,tool,version,missing_contract_fields}`

## 3. 证据条目 schema

```json
{"id":"fig1","locator":"materials/paper.pdf#p3","quote":"原句逐字片段","status":"verified"}
```

`status` 四档：`verified`（locator+逐字 quote 命中）、`inferred`（有 locator，转写/概括）、
`not_reported`（材料未报告）、`conflicting`（多来源不一致，两处都记录）。

## 4. 回填规则

- 契约字段在 payload 中存在则取原值；否则按类型给 `[]`/`{}`/`null`；不改名、不删未知键。
- 别名字段 `changes` 与 `change_log` 同源；`contract_keys()` 对 dict/list 两种 output_contract 都识别。
- 任何中途异常都先写 legal partial 结果，不留空产物（`skeleton` 第一步执行）。
