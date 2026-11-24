# result.json 输出契约（复核基准）

本文件是 `/app/output/result.json` 的字段契约，也是 `scripts/core_impl.py` 校验、
合并与渲染的判定依据。机器读 result.json，人读 `/app/output/report.md`。

## 1. 顶层字段

| 字段 | 类型 | 取值 / 说明 |
|---|---|---|
| `case_id` | string | 来自 `case.json` 的 `case_id`/`id`/`caseId`；缺失时为 `"unknown"` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error`，须诚实 |
| `summary` | object | `verdict` + `counts` + 分类计数（见 §2） |
| `review_report` | object | 含 `title` 与 `findings`（逐条问题） |
| `result_status` | string | 通过 / 问题 / 待复核 / 未检查（含括号说明） |
| `evidence` | object | `evidence_map`：每条 finding 的定位 + 逐字摘录 + 依据 |
| `sources` | array | 只含实际核验过的来源（文件名/页/段），不虚构 DOI/URL |
| `uncertainty` | array | 无法确认的条目与原因 |
| `pending_human` | array | 需人工复核/决策的条目 |
| `limitations` | array | 未完成检查项、材料缺口、工具缺口 |
| `errors` | array | 输入/解析/写盘错误（dict：`code`+`message`） |
| `degradations` | array | 降级处置记录（dict：`code`+`message`） |
| `numeric_checks` | array | 脚本自动生成的数值一致性**候选**（非结论） |
| `change_log` | array | 每次落盘的变更记录 |
| `runtime` | object | 引擎/版本/预算；**不写墙钟时间**以保证可重复 |

除上述规范字段外，若 `case.json` 含 `output_contract`，其**全部字段**都要出现在
结果里：与规范字段同名的，填入真实/空数据；其余未知键**原样保留**（不改名、不
删除、缺值保持 `null`/`[]`/`""`）。若契约指定顶层 `findings`，它是对
`review_report.findings` 的别名，自动同步。

## 2. `summary`

```json
{
  "verdict": "pass|problems_found|needs_review|unchecked|insufficient_evidence|input_error",
  "counts": {"total": 0, "critical": 0, "major": 0, "minor": 0, "advisory": 0, "pending": 0},
  "by_facet": {...},          // 五个检查面各计数
  "by_finding_type": {...},   // 五类缺陷各计数
  "by_check_class": {...}     // 六类审查面各计数
}
```

## 3. 一条 finding 的字段

六必填：`id` / `facet` / `locator` / `quote` / `severity` / `suggestion`。
语义完整字段：`finding_type` / `check_class` / `confidence` / `review_status` /
`basis`。

| 字段 | 必填 | 合法值 |
|---|---|---|
| `id` | 是 | 稳定编号，建议 `F001`、`F002`…（排序键） |
| `facet` | 是 | `research_design` / `variable_definition` / `statistical_methods` / `sample_size_power` / `results_reporting` |
| `locator` | 是 | 可回查定位串：`p.3 ¶2`、`p.4 Table 1`、`p.6 Fig.2`、纯文本 `¶2`；含文件名/页/段/表/图 |
| `quote` | 是 | 逐字摘自原文（来自 `texts/*.txt`），不改写、不拼接非相邻文字；≤1200 字符 |
| `severity` | 是 | `critical` / `major` / `minor` / `advisory` / `pending` |
| `suggestion` | 是 | 可执行修改建议 |
| `finding_type` | 否（默认 `pending_review`） | `factual_error` / `insufficient_evidence` / `normative_issue` / `advisory` / `pending_review` |
| `check_class` | 否（默认 `method_design`） | `fact_check` / `citation_support` / `method_design` / `statistical_reporting` / `argumentation_chain` / `academic_norms` |
| `confidence` | 否（默认 `low`） | `high` / `medium` / `low` |
| `review_status` | 否（默认 `needs_review`） | `confirmed` / `needs_review` / `not_verified` |
| `basis` | 否 | 判断依据（方法学规则、报告规范、原文事实） |

## 4. `evidence.evidence_map`

以 finding `id` 为键：

```json
{"F001": {"locator": "p.3 ¶2", "quote": "…", "source_file": "…", "basis": "…"}}
```

复核人凭 `locator + quote` 回读原文即能独立复现结论。

## 5. 降级码（`degradations[].code`）

| 码 | 含义 | 处置 |
|---|---|---|
| `SIZE_SKIP` | 文件超上限被跳过 | 记录，继续处理其余材料 |
| `ENC_FALLBACK` | 非 UTF-8 编码被回退解码 | 记录实际编码 |
| `JSON_BAD` / `JSON_EMPTY` / `JSON_SALVAGED` | case.json/材料 JSON 异常 | 记录，继续读其它材料 |
| `PDF_NO_TOOL` / `PDF_FAIL` / `PDF_EMPTY` | PDF 抽取失败/空文本 | 该 PDF 标 `needs_ocr`，不硬判 |
| `DOCX_FAIL` / `DOCX_STRUCT` | docx 无法解析 | 记录，正文可用则审正文 |
| `TASK_MISSING` | task.md 缺失 | 记录，按通用检查面继续 |
| `UNSUPPORTED_KIND` / `TOO_MANY_FILES` | 不支持类型/文件过多 | 跳过并记录 |
| `QUOTE_TRUNCATED` | 摘录超长被截断 | 记录截断位置 |
| `WRITE_FAIL` | 写盘失败 | 尝试最小合法产物，仍失败则退出非 0 |

## 6. 诚实与确定性

- `status` 必须诚实：材料不足写 `insufficient_evidence`，输入坏写 `input_error`；
  未完成写 `partial`，不冒充 `ok`。
- 禁止虚构引用、DOI、URL、数值、来源、软件状态与评测分数；缺失写
  `not_reported`/`not_verified`/`needs_review`。
- 同一输入重复运行结果**字节级一致**：`sort_keys=True`、stable 排序、无随机数、
  `runtime` 不写墙钟时间。
