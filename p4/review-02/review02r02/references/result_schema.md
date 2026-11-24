# result.json 完整契约（本 Skill 的输出 Schema）

本文件是 `scripts/*.py` 与 Agent 共用的输出契约。人工复核与自检脚本均以此为准；
任何字段缺失都不得用编造值填充，缺值写 `null` / `[]` / `""` / `not_reported` /
`not_verified`。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 案例标识；取自 case.json 或 task.md，取不到写 `unknown` |
| `status` | string | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `summary` | object | 是 | 总判定与计数，见 §2 |
| `review_report` | object | 是 | 结论清单与覆盖矩阵，见 §3 |
| `evidence` | object | 是 | 证据映射，见 §4 |
| `sources` | array | 是 | 实际核验过的来源清单；未取得写 `not_verified` |
| `uncertainty` | array | 是 | 不确定项；字符串或对象均可 |
| `pending_human` | array | 是 | 待人工复核项 |
| `limitations` | array | 是 | 局限与未完成项（必须诚实） |
| `errors` | array | 是 | 读文件/坏 JSON/坏编码等运行错误 |
| `degradations` | array | 是 | 降级记录（缺 pdftotext、扫描版、超限文件等） |
| `change_log` | array | 是 | 阶段检查点；由 state.json 的 checkpoints 重建 |
| `runtime` | object | 是 | 运行时与预算台账，见 §5 |
| `output_contract` | object/list | 条件 | case.json 含 output_contract 时回填原样副本 |

`output_contract` / `input_contract` 的全部字段**原样回填到顶层**：不改名、不删未知
键，缺值给 `null`；标准字段（case_id/status/summary/...）同名时优先于契约字段。

## 2. summary

```json
{
  "verdict": "pass | issues_found | needs_review | unchecked | insufficient",
  "counts": {
    "total": 0, "critical": 0, "major": 0, "minor": 0, "suggestion": 0,
    "by_category": {
      "citation_support": 0, "fact_source": 0, "method_design": 0,
      "numerical_statistical": 0, "argumentation": 0, "academic_norm": 0
    }
  }
}
```

`verdict` 计算规则（finalize 自动判定，meta.json 的 `overall_verdict` 可覆盖）：

- 有 critical/major/minor 级 finding → `issues_found`
- 只有 suggestion 级 finding，或六类覆盖矩阵仍有 `unchecked` → `needs_review`
- 无 finding 且六类全部非 unchecked → `pass`
- 无材料 → `insufficient`

## 3. review_report

```json
{
  "findings": [ { /* 见 §3.1 */ } ],
  "coverage": {
    "citation_support": "checked | unchecked",
    "fact_source": "checked | unchecked",
    "method_design": "checked | unchecked",
    "numerical_statistical": "checked | unchecked",
    "argumentation": "checked | unchecked",
    "academic_norm": "checked | unchecked"
  },
  "sections_covered": ["abstract", "methods", "results", "discussion"]
}
```

### 3.1 finding 对象（每条结论一个文件，放 `/app/work/findings/<id>.json`）

必填七键（缺一不可，值必须为非空字符串）：

- `id`：稳定编号，如 `F001`；`category`：六类 canonical key 之一；
- `locator`：可回查定位串（`p.3, para 2`、`Table 1, row 3`、`Fig 2b`、`materials/paper.md#L42`）；
- `quote`：**逐字**摘自原文的片段（不改词、不增删关键数字；省略用 `…` 标记）；
- `verdict`：引用支持四档（见 checklist.md）或事实核验判定；
- `severity`：`critical` / `major` / `minor` / `suggestion`；
- `suggestion`：修改建议（可执行、指向明确）。

选填键：`finding_type`、`claim`（对断言的中立转述）、`basis`（判定依据）、
`source_ref`（被核验来源，未取得写 `not_verified`）、`confidence`、
`review_status`（`recorded` / `verified` / `needs_review`）。

## 4. evidence

```json
{
  "evidence_map": {
    "F001": {
      "locator": "p.3, para 2",
      "quote": "逐字摘录",
      "basis": "判定依据",
      "source_ref": "not_verified"
    }
  }
}
```

`evidence_map` 与 findings 一一对应，由 finalize 自动生成，禁止手工增删不一致。

## 5. runtime（预算台账）

```json
{
  "started_at": "ISO-8601", "finished_at": "ISO-8601",
  "requests_used": 0, "phase": 0,
  "budget": {
    "total_seconds": 900, "total_requests": 64,
    "phase_budgets": [
      {"phase": 0, "seconds": 180, "requests": 8, "commit": "legal skeleton"},
      {"phase": 1, "seconds": 300, "requests": 20, "commit": "findings"},
      {"phase": 2, "seconds": 300, "requests": 25, "commit": "verified"},
      {"phase": 3, "seconds": 120, "requests": 11, "commit": "final + selftest"}
    ]
  }
}
```

`requests_used` 与 `phase` 由 `scripts/main.py checkpoint` 更新，写入
`/app/work/state.json`，finalize 时同步进 result.json；不虚构平台实测分数。

## 6. 确定性

同一输入重复运行 `finalize`，`summary` / `review_report` / `case_id` 必须逐字节一致
（脚本无随机数、无网络、无外部依赖）。`runtime.finished_at` 等时间戳是簿记元数据，
允许变化。

## 7. 最小可复核示例（示意，非正式案例结果）

一个 finding 文件（`/app/work/findings/F001.json`）长这样：

```json
{
  "id": "F001",
  "category": "numerical_statistical",
  "finding_type": "inconsistency",
  "severity": "major",
  "verdict": "unsupported",
  "locator": "p.2, para 1 vs Table 1, row 3",
  "quote": "N=120 patients were enrolled … as shown in Table 1, N=98.",
  "claim": "正文与表格报告的样本量不一致（转述）。",
  "basis": "两处均逐字摘录，数值 120 与 98 冲突，同一指标应一致。",
  "source_ref": "not_verified",
  "suggestion": "核对原始病例表，统一 N 值并说明排除标准。",
  "confidence": "high",
  "review_status": "verified"
}
```

`finalize` 据此生成 report.md，并把该条写入 `evidence.evidence_map["F001"]`。
`quote` 中的 `…` 表示删除省略，其余字符必须与原文逐字一致。

report.md 结构固定为：标题 → 状态行（case_id/status/verdict/计数）→ 覆盖矩阵
（六类 × 状态）→ 问题清单（按类别分组，每类下列出 finding）→ 不确定性与局限。
report.md 供人工阅读与编辑，机器复核一律以 result.json 为准。
