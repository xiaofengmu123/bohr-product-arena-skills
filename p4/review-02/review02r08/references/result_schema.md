# result.json 完整契约（机器可复核）

`/app/output/result.json` 是 Skill 的主产物。所有字段均为 JSON 值；`scaffold` 命令
生成的骨架已含全部标准字段，agent 只需增量写入 findings 与 meta。

## 1. 顶层字段

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 来自 `/input/case.json` |
| `status` | string | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `summary` | object | 是 | 见 §2 |
| `review_report` | object | 是 | 含 `findings` 数组，见 §3 |
| `evidence` | object | 是 | 含 `evidence_map`，见 §4 |
| `sources` | array | 是 | 实际核验过的来源，见 §5 |
| `uncertainty` | array | 是 | 无法确认的条目说明 |
| `pending_human` | array | 是 | 需人工复核的条目 |
| `limitations` | array | 是 | 未完成的检查项与原因 |
| `errors` | array | 是 | 运行期错误 |
| `degradations` | array | 是 | 降级项（如 needs_ocr） |
| `change_log` | array | 是 | 每次 finalize 的版本记录 |
| `runtime` | object | 是 | 见 §6 |
| `output_contract.*` | 任意 | 若存在 | `case.json` 的 output_contract 全量回填，见 §7 |

## 2. summary

```json
{
  "verdict": "issues_found",
  "counts": {
    "total": 3,
    "critical": 0,
    "major": 2,
    "minor": 1,
    "suggestion": 0,
    "by_category": {
      "citation_support": 1,
      "fact_source": 0,
      "evidence_location": 1,
      "numerical_statistical": 1,
      "argumentation": 0,
      "academic_norm": 0
    }
  }
}
```

- `verdict` 取值：`pass`（无问题）/ `issues_found`（有问题）/ `needs_review`（有
  待复核项）/ `unchecked`（尚未审完）/ `insufficient`（材料不足）。
- `counts.total` 必须等于 `findings` 数组长度；`by_category` 键与 `category`
  取值一致。

## 3. finding 对象

每条 finding 含下列字段，**前七项必填**：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | `F001`、`F002`…全局唯一 |
| `category` | 是 | 六类 canonical key 之一 |
| `locator` | 是 | 可回查定位串 |
| `quote` | 是 | 逐字摘录原文 |
| `verdict` | 是 | 引用支持四档或 `confirmed`/`needs_review`/`not_verifiable` |
| `severity` | 是 | `critical`/`major`/`minor`/`suggestion` |
| `suggestion` | 是 | 修改建议 |
| `finding_type` | 否 | §3 finding_type 之一 |
| `claim` | 否 | 对被审断言的转述 |
| `basis` | 否 | 判定依据（与引文语义对照） |
| `source_ref` | 否 | 被引来源定位；未取得写 `not_verified` |
| `confidence` | 否 | `high`/`medium`/`low` |
| `review_status` | 否 | `reviewed`/`needs_review`/`not_reviewed` |

`quote` 与 `locator` 必须能回查到原文；转述放 `claim`，不得混入 `quote`。

## 4. evidence.evidence_map

数组，每条 finding 对应一项：

```json
{
  "finding_id": "F001",
  "locator": "p.3, para 2",
  "quote": "…原文逐字片段…",
  "basis": "引文只报告 A 人群，正文结论推广到 B 人群，故 partially_supported",
  "source_ref": "ref [12] / DOI: …"
}
```

用于人工一键复核；同一指标冲突时两个 finding 各带各自证据，冲突标 `inconsistency`。

## 5. sources / uncertainty / pending_human / limitations

- `sources`：`{"ref": "参考文献编号或 DOI/URL", "kind": "paper|dataset|code|…",
  "verified": true|false, "note": "…"}`。仅填确实核验过的；未取得写
  `verified: false` 或直接在 `source_ref` 写 `not_verified`。
- `uncertainty`：`{"finding_id": "F003", "issue": "引文仅见摘要，无法判支持范围"}`。
- `pending_human`：`{"finding_id": "F004", "reason": "需人工确认原始数据"}`。
- `limitations`：字符串数组，写未覆盖章节/未完成的检查项与原因。

## 6. runtime

```json
{
  "started_at": "2026-11-22T08:00:00Z",
  "finished_at": "2026-11-22T08:12:34Z",
  "seconds": 754,
  "model_requests": 38,
  "scaffold_landed": true
}
```

时间与请求数如实记录，不虚构；中断未完成时 `finished_at` 可为空并保持 `partial`。

## 7. output_contract 回填

若 `/input/case.json` 含 `output_contract` 对象：

- 其全部键**原样**复制到 result.json 顶层（标准字段优先，不覆盖标准字段）。
- 缺失值给 `null`；数组给 `[]`；字符串给 `""`；不改名、不删未知键。
- `scaffold` 命令自动完成；agent 不要手工重写。

## 8. 状态与判定的组合约定

| 情况 | status | verdict |
|---|---|---|
| 完整审查、可复核 | `ok` | `pass`/`issues_found`/`needs_review` |
| 中途中断但有合法骨架 | `partial` | `unchecked`/`needs_review` |
| 材料缺失/全不可读 | `insufficient_evidence` | `insufficient` |
| 输入坏 JSON/缺 case.json | `input_error` | `unchecked` |
