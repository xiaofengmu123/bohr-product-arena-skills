# result.json 完整契约

本文件定义 `/app/output/result.json` 与 `/app/output/report.md` 的字段与取值。
`scripts/main.py` 的 `scaffold` 会先落盘合法骨架，`finalize` 合并 findings 后刷新；
两者都是确定性的（无时间戳、无随机、稳定排序）。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识，取自 `case.json` 的 `case_id`/`id`，缺失为 `unknown_case` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error`，须诚实 |
| `output_contract` | object | `case.json` 的 `output_contract` **原样回填**（缺失给 `{}`，不改名不删未知键） |
| `summary` | object | `verdict` + `counts`（见 §2） |
| `review_report` | object | `{ "findings": [...] }`（见 §3） |
| `evidence` | object | `{ "evidence_map": [...] }`，由 findings 派生，保证一致 |
| `sources` | list | 实际核验过的来源；未取得写 `not_verified` |
| `uncertainty` | list | 无法确定/来源冲突的记录 |
| `pending_human` | list | 待人工复核项 |
| `limitations` | list | 未完成的检查项、权限/数据缺口 |
| `errors` | list | 解析失败、finding 缺键等错误 |
| `degradations` | list | 降级处置（缺 pdftotext、扫描版 PDF、坏编码等） |
| `change_log` | list | 阶段与变更记录（来自 `work/meta.json`，逐条说明） |
| `runtime` | object | 确定性元信息（见 §5），**不含墙钟时间** |

## 2. summary

- `summary.verdict`：`pass | issues_found | needs_review | unchecked | insufficient`。
- `summary.counts`：
  - `total` / `critical` / `major` / `minor` / `suggestion`
  - `by_category`：六类 canonical key 各自的条数（六类都出现，缺失为 0）。

## 3. finding 对象（`review_report.findings[]`）

每条 finding 必填：

`id`、`category`、`finding_type`、`severity`、`verdict`、`locator`、`quote`、`suggestion`。

其中 `id` 缺省时由脚本按 `sha256(category|locator|quote)` 派生（稳定、跨运行不变）；
`finding_type` 缺省时脚本补 `unspecified` 并在 `errors` 记录一条提醒。

| 字段 | 说明 |
|---|---|
| `id` | 稳定标识，如 `F-1A2B3C4D` |
| `category` | 六类 canonical key 之一（见 checklist.md §1） |
| `finding_type` | 细分类型（见 checklist.md §3） |
| `severity` | `critical / major / minor / suggestion` |
| `verdict` | 引用支持四档；其他类别 `confirmed / needs_review / not_confirmed` |
| `locator` | 可回查定位串（页/段/表/图/行/数据集条目） |
| `quote` | **逐字**摘自原文，不改词、不增删关键数字 |
| `claim` | 对被审查断言的转述（须与原文区分） |
| `basis` | 判定依据：引文语义、数据、方法等 |
| `source_ref` | 来源定位（DOI/URL/参考文献编号/数据集条目），未取得为 `not_verified` |
| `suggestion` | 修改建议 |
| `confidence` | 可选：`high / medium / low` |
| `review_status` | 可选：`needs_review` 表示待复核 |

`evidence.evidence_map[]` 的每项由 finding 派生：
`finding_id + category + locator + quote + basis + source_ref + severity + verdict`，
因此永远与 findings 同步，不额外维护。

## 4. 状态判定（脚本内置，保持诚实）

- 输入目录不存在 → `input_error`。
- 无任何可读材料 → `insufficient_evidence`，`verdict=insufficient`。
- 有材料但 `meta.json` 未写 `overall_verdict` → `partial`；有 findings 时 `verdict=issues_found`，否则 `unchecked`。
- `meta.json` 写了 `overall_verdict`：`final=true` → `ok`，`final=false` → `partial`，`verdict` 取 `overall_verdict`。

## 5. runtime（确定性）

```json
{
  "engine": "citation-fact-audit",
  "script_version": "1.0.0",
  "python": "3.x.y",
  "deterministic": true,
  "budget": {"seconds": 900, "model_requests": 64},
  "stats": {"materials": 0, "pages": 0, "chars": 0}
}
```

墙钟耗时与模型原生 trace 由平台记录，不写进 `result.json`，以保证同一输入两次运行
产物逐字节一致。`work/state.json` 仅记录内部阶段，不作为交付物。

## 6. report.md

`report.md` 是 `result.json` 的人类可读渲染：头部 case_id/status/verdict、摘要计数、
按六类分组的 finding（逐条含定位、逐字摘录、转述、依据、来源、建议），以及来源、
不确定性、待复核、限制、错误、降级、变更记录各节。它由 `finalize` 确定性生成，可编辑。
