# result.json 输出契约（逐字段）

本文件是 `scripts/core_impl.py` 中可执行校验逻辑（`validate_result`）的人类可读版本。
执行前必须完整阅读；每写一条 finding 或刷新一次产物，都要保证结果不违反本契约。
`python -B scripts/main.py check` 会按同一份规则校验产物，退出码 0 表示无硬错误。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 含义与约束 |
|---|---|---|---|
| `case_id` | string | 是 | 案例标识；来自 `/input/case.json` 的 `case_id`/`id`，缺失时由脚本按输入文件确定性派生 |
| `status` | string | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error`，须诚实反映完成度 |
| `summary` | object | 是 | 见 §2 |
| `review_report` | object | 是 | `{ "findings": [...] }`，见 §3 |
| `evidence` | object | 是 | `{ "evidence_map": [...] }`，见 §4 |
| `sources` | list | 是 | 实际核验过的来源；未取得的来源在 finding 级写 `not_verified`，不得虚构 DOI/URL |
| `uncertainty` | list | 是 | 无法确认的事项（每条含 finding_id/reason/status） |
| `pending_human` | list | 是 | 需人工复核的事项 |
| `limitations` | list | 是 | 缺口、未覆盖检查项、材料不足说明 |
| `errors` | list | 是 | 运行与契约错误（脚本自动追加 `contract: ...` 前缀） |
| `degradations` | list | 是 | 缺软件、扫描 PDF、坏编码、超限文件等降级记录 |
| `change_log` | list | 是 | 每次内容变更追加一条，见 §5；是审计线索，不是装饰字段 |
| `runtime` | object | 是 | 起止时间、耗时与脚本版本；时间戳被排除在内容哈希之外 |

此外，若 `/input/case.json` 含 `output_contract`，其**全部键**须原样回填到顶层：
不改名、不删未知键；无值的键按类型给 `null` / `[]` / `""`。标准字段优先于同名回填。

## 2. summary

```json
{
  "verdict": "unchecked",
  "counts": {
    "total": 0, "critical": 0, "major": 0, "minor": 0, "suggestion": 0,
    "by_category": { "citation_support": 0, "...": 0 }
  }
}
```

- `verdict`：`pass` / `issues_found` / `needs_review` / `unchecked` / `insufficient`。
- `counts.*` 必须为整数；`by_category` 用七类 canonical key，见 `references/checklist.md`。

## 3. finding 字段（review_report.findings[]）

七必填键：`id` / `category` / `locator` / `quote` / `verdict` / `severity` / `suggestion`。

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 稳定标识，如 `F001`；同一案例内唯一、按发现顺序递增 |
| `category` | 是 | 七类 canonical key 之一 |
| `locator` | 是 | 可回查定位串：`p.3, para 2`、`Table 1, row 3`、`materials/paper.txt#L42` |
| `quote` | 是 | **逐字**摘录原文；不增删关键词、不把转述当原文 |
| `verdict` | 是 | 引用支持四档或该类别的判定（`fully_supported` / `partially_supported` / `unsupported` / `undeterminable`） |
| `severity` | 是 | `critical` / `major` / `minor` / `suggestion` |
| `suggestion` | 是 | 修改建议，必须可执行 |
| `finding_type` | 否 | 细分类，见 checklist §3 |
| `claim` | 否 | 对被审断言的转述，须标注为转述 |
| `basis` | 否 | 判定依据（为什么这样判，引文语义如何与被审结论对齐/冲突） |
| `source_ref` | 否 | 被引来源定位；未取得写 `not_verified` |
| `confidence` | 否 | `high` / `medium` / `low` |
| `review_status` | 否 | `confirmed` / `needs_review`；无法确认必须 `needs_review` |

## 4. evidence.evidence_map

每条 finding 一条记录，键为 `finding_id` / `locator` / `quote` / `basis` / `source_ref`。
它由 `finalize` 自动从 findings 重建，保证"清单里的每条结论"与"可回查证据"一一对应。

## 5. change_log

| 字段 | 说明 |
|---|---|
| `ts` | UTC ISO 时间戳 |
| `phase` | `scaffold` / `finalize` / 后续人工阶段 |
| `action` | 变更动作（`created_skeleton` / `merged_findings` / `refreshed_skeleton`） |
| `finding_count` | 该次变更后的 finding 数 |
| `verdict` | 该次变更后的总体判定 |
| `content_hash` | 内容哈希（排除 runtime/change_log），用于幂等与审计 |

规则：内容未变化时**不追加**条目，重复运行 `finalize` 结果一致（幂等）。

## 6. 最小合法结果（示例，非正式案例）

```json
{
  "case_id": "case-demo",
  "status": "ok",
  "summary": {"verdict": "issues_found", "counts": {"total": 1, "critical": 0,
    "major": 1, "minor": 0, "suggestion": 0, "by_category": {"numerical_statistical": 1}}},
  "review_report": {"findings": [
    {"id": "F001", "category": "numerical_statistical", "finding_type": "inconsistency",
     "severity": "major", "verdict": "unsupported", "locator": "materials/paper.txt#L3",
     "quote": "N=10", "claim": "样本量为 10（转述）", "basis": "摘要未给 N，与结果段不一致",
     "source_ref": "not_verified", "suggestion": "在摘要补齐 N 并保持一致", "confidence": "medium",
     "review_status": "confirmed"}
  ]},
  "evidence": {"evidence_map": [{"finding_id": "F001", "locator": "materials/paper.txt#L3",
    "quote": "N=10", "basis": "摘要未给 N，与结果段不一致", "source_ref": "not_verified"}]},
  "sources": [], "uncertainty": [], "pending_human": [], "limitations": [],
  "errors": [], "degradations": [],
  "change_log": [{"ts": "2026-01-01T00:00:00+00:00", "phase": "finalize",
    "action": "merged_findings", "finding_count": 1, "verdict": "issues_found",
    "content_hash": "0123456789abcdef"}],
  "runtime": {"started_at": "2026-01-01T00:00:00+00:00", "finished_at": null,
    "duration_seconds": null, "script_version": "1.0.0"}
}
```

说明：`by_category` 只列出有计数者为示意；实际产物含七类 canonical key（零值也保留）。
时间戳与 `content_hash` 为示意值，运行时由脚本确定性生成。
