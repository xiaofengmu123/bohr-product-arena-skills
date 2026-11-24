# result.json 完整契约与脚本用法

本文件定义 `/app/output/result.json` 的字段语义、`/app/work` 中间产物的格式，
以及三个脚本的调用方式。字段值均取自已下合法枚举。

## 1. 顶层字段（result.json）

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 来自 `/input/case.json` 的 `case_id`；缺失时 `"unknown"` |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error` |
| `result_status` | enum | `pass \| issues \| needs_review \| unchecked` |
| `summary` | object | `verdict` + `counts`（按严重度）+ `by_facet` + `by_type` |
| `coverage` | object | 五个 facet 与六类 check_class 的覆盖状态矩阵 |
| `review_report` | object | `findings` 数组，逐条问题 |
| `evidence` | object | `evidence_map`（定位+摘录+依据）+ `conflict_index`（冲突项） |
| `sources` | array | 实际核验过的来源（材料文件 + 外部规范），无虚构 DOI/URL |
| `uncertainty` | object | `items`（不确定项）+ `pending_human`（待人工确认问题） |
| `limitations` | array | 未完成检查项、能力边界、图表无法解析等 |
| `errors` | array | 输入/解析错误（坏 JSON、坏编码等） |
| `degradations` | array | 降级记录，每条含稳定 `code` |
| `change_log` | array | 确定性变更记录（`seq` 序号而非墙钟时间） |
| `runtime` | object | `engine/python_version/deterministic/source_timestamp/scripts` |
| `numeric_checks` | object | `candidates` 数组（候选，须人工确认） |
| `validation` | array | finalize 的自动校验结果（`level/severity` 违规项） |
| （output_contract 字段） | 任意 | case.json 的 `output_contract` 全部键**原样回填**，缺值给 `null`，不改名、不删未知键 |

`status` 诚实派生：缺 case.json 且无材料 → `input_error`；无可用文本 →
`insufficient_evidence`；材料可用但审查未完成 → `partial`；审查完成且五个 facet 均
已检查 → `ok`。`result_status` 由 findings 与 meta 派生，见 main.py 的
`_derive_result_status`。

## 2. finding 字段（`/app/work/findings/<id>.json`，一文件一对象）

| 字段 | 必填 | 合法值 |
|---|---|---|
| `id` | 是 | `F001` 式编号，唯一 |
| `facet` | 是 | 五个 facet 之一 |
| `check_class` | 是 | 六个 check_class 之一 |
| `finding_type` | 是 | 五类缺陷之一 |
| `severity` | 是 | 五档严重度之一 |
| `locator` | 是 | 可回查定位串，如 `p.4 ¶2`、`¶3` |
| `quote` | 是 | 逐字摘录 |
| `statement` | 是 | 一句缺陷结论 |
| `suggestion` | 是 | 修改建议 |
| `basis` | 是 | `verbatim \| inferred \| external \| not_verified` |
| `evidence_grade` | 是 | `confirmed \| probable \| not_verified \| not_reported` |
| `confidence` | 是 | `high \| medium \| low` |
| `review_status` | 是 | `confirmed \| needs_review` |
| `conflicts` | 否 | 冲突位置数组（source_conflict 时给出两处 locator） |

`finalize.py` 只合并、不改写这些字段；非法枚举/缺必填会进 `validation`，不静默丢弃。

## 3. 工作目录中间产物（`/app/work`）

- `manifest.json`：发现的输入文件清单与解析状态（`discover` 产出）。
- `segments.json`：文本段索引（`id/source/kind/locator/text/chars/leaf`）。
- `texts/<name>.txt`：每份材料的逐字抽取文本。
- `terms.json`：统计/方法关键词命中（`hits` + `frequency`）。
- `findings/<id>.json`：Agent 逐条写入的 finding。
- `meta.json`：Agent 写审查总况（见 §4）。
- `state.json`：流程状态（`phase`、findings 计数等，`scaffold` 产出）。

## 4. meta.json 契约（Agent 在阶段 2–3 填写）

```json
{
  "status": "partial",
  "complete": false,
  "overall_verdict": "",
  "overall_assessment": "",
  "result_status": "unchecked",
  "checked_facets": [],
  "checked_classes": [],
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "limitations": [],
  "errors": [],
  "degradations": [],
  "change_log": []
}
```

`complete=true` 且五个 facet 都在 `checked_facets` 中时，finalize 才可能把
`status` 提为 `ok`；否则保持 `partial`。`uncertainty`/`pending_human` 每项建议带
`id/statement/reason/affects`。

## 5. 降级码（degradations[].code）

| code | 含义 | 处置 |
|---|---|---|
| `D_EMPTY_INPUT` | 无任何材料文件 | status=`insufficient_evidence` |
| `D_MISSING_CASE` | 缺 case.json | case_id=`unknown`，记录 errors |
| `D_BAD_JSON` | JSON 无法解析 | 记 errors，跳过该文件继续 |
| `D_BAD_ENCODING` | 编码不可读 | 记 errors，跳过该文件 |
| `D_OVERSIZE_FILE` | 单文件 > 2MB | 跳过并记录 |
| `D_PDF_NO_TEXT` | PDF 无可抽取文本（扫描件） | 标 `needs_ocr`，不判结论 |
| `D_NO_PDFTOTEXT` | 无 pdftotext | 该 PDF 标 `needs_ocr` |
| `D_FIGURE_UNPARSED` | 图表无法解析 | 记录 limitations，审正文 |
| `D_SOURCE_CONFLICT` | 来源版本不一致 | 两条都记录，进 uncertainty |
| `D_WRITE_FAIL` | 写盘失败 | 降级到最小合法产物 |

## 6. 脚本用法（包根目录 `/skills/weekly-skill` 下运行）

```bash
python -B scripts/main.py discover  --input /input --work /app/work
python -B scripts/main.py scaffold  --input /input --output /app/output --work /app/work
python -B scripts/main.py finalize  --input /input --output /app/output --work /app/work
python -B scripts/main.py --help    # 退出码 0
python -B scripts/selftest.py       # 自检，退出码 0 = 通过
```

三个脚本仅依赖 Python 标准库；`--input/--output/--work` 缺省分别为
`/input`、`/app/output`、`/app/work`。无随机数，同一输入重复运行结果一致。
