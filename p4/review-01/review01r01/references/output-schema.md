# result.json 输出契约（字段规格）

最终产物 `/app/output/result.json` 顶层由三部分组成：
**信封字段 + output_contract 全部字段 + 审查主结构**。`case_id` 必须原样保留。
`review_report.md` 是同一数据的可读文本视图，由 `build` 自动生成。

## 1. 信封字段（追加，不得与 contract 键冲突）

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string/null | 原样回填 `case.json` 的 case_id，不得丢失 |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error` |
| `sources` | list | 稿件来源：`[{source_id, name, path, kind, locator_count, problems}]` |
| `uncertainty` | list | 歧义/冲突/无法核验项，字符串或对象 |
| `pending_human` | list | 待人工复核项 |
| `errors` | list | 解析/读取错误记录 |
| `degradations` | list | 降级记录（缺文本层/缺 OCR/超长跳过等） |
| `changes` | list | 相对基础 Agent 或上版的改进记录 |
| `change_log` | list | `[{step, at, note}]` 增量变更日志 |
| `runtime` | object | `{seconds, generated_at}` 易变量只放这里 |

`status` 判定：
- `ok`：六类中与本文相关的 facet 均已查并有结果；
- `partial`：部分完成，`unchecked_items`/`pending_human` 写明缺口；
- `insufficient_evidence`：材料不可用或无法形成任何可靠结论；
- `input_error`：case.json 坏 JSON 等输入错误（但材料仍可能可审时用 partial）。

## 2. output_contract 回填规则

`case.json` 的 `output_contract`（dict 或 list）全部字段必须原样并入 result.json：
- dict：每个 key 成为顶层字段，Agent 在 draft.json 顶层提供同名值则并入；否则写 `null`（list 型写 `[]`）；
- list：每个元素（dict 的 name/key/field 或纯字符串）成为顶层字段，缺值写 `null`；
- 不改名、不删除未知键，`output_contract` 本身也保留一份原文供追溯。

## 3. 审查主结构 `result`

```json
"result": {
  "findings": [],
  "unchecked_items": [],
  "verdict_summary": {}
}
```

### 3.1 finding 字段（`result.findings[]`）

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `finding_id` | string | 是 | F01、F02… 稳定编号 |
| `facet` | enum | 是 | 六类之一 |
| `severity` | enum | 是 | `critical/major/minor/suggestion` |
| `issue_type` | enum | 是 | `factual_error/insufficient_evidence/standards_issue/suggestion` |
| `verdict` | enum | 是 | `confirmed/to_review/not_checked` |
| `claim` | string | 是 | 被审查论断或问题描述（可独立复核，非整段复述） |
| `location` | string | 是 | 主论断定位，如 `para7` 或 `p3#para2` |
| `quote` | string | 是 | 主论断**逐字原文**（可跨行、不跨段） |
| `rationale` | string | 是 | 判定依据：引用 review-checklist 编号（如 `SR-1`）+ 为什么成立 |
| `revision_suggestion` | string | 是 | 可执行修改动作 |
| `confidence` | enum | 是 | `high/medium/low` |
| `check_anchor` | string | 否 | 判据编号，如 `CS-3` |
| `evidence` | list | 是 | 证据项，**每条关键结论至少 1 条**（见 3.2） |
| `conflicts` | list | 否 | 内部不一致双证（见 3.3） |

### 3.2 证据项 `evidence[]`（证据溯源硬要求）

每条证据 = 一个"来源位置 + 逐字原文 + 与论断的关系"三元组：

```json
{
  "evid_id": "E01",
  "role": "supporting_citation|data_point|method_detail|definition|internal_consistency|figure_claim",
  "source_id": "D1",
  "locator": "p7#para3",
  "quote": "证据逐字原文",
  "relation": "supports|contradicts|unverifiable|not_found"
}
```

- `role` 说明证据扮演的角色；`relation` 说明证据与论断的关系。
- 引用支持类 finding 必须有 `supporting_citation` 证据项，明确"本文论断 quote ↔ 被引文献主张 quote"的对照。
- 全部 `quote` 必须经 `check` 逐字命中原文（归一化空白后子串匹配）。

### 3.3 冲突项 `conflicts[]`（可选）

同一稿件两处表述不一致时记录双证，不擅自取舍：

```json
{"locator_a":"p1#para1","quote_a":"...","locator_b":"p4#para2","quote_b":"...","kind":"source_conflict"}
```

### 3.4 `unchecked_items[]` 与 `verdict_summary`

```json
"unchecked_items": [{"facet":"citation_support","reason":"参考文献缺失，无法核验"}]
```

`verdict_summary` 由 `build` 自动派生，含 totals、by_facet、by_severity、
by_issue_type、by_verdict、coverage（checked_facets / unchecked_facets）。

## 4. 顶层 `evidence_map`（本版强化点）

`build` 从 findings 自动派生，与 `finding.evidence` 严格一致，供全局交叉检索：

```json
"evidence_map": {
  "F01": {
    "facet": "citation_support",
    "claim": {"locator":"p3#para2","quote":"本文论断逐字原文"},
    "evidence_count": 1,
    "relations": {"supports":1,"contradicts":0,"unverifiable":0,"not_found":0},
    "items": [{"evid_id":"E01","role":"...","source_id":"D1","locator":"...","quote":"...","relation":"..."}],
    "conflicts": []
  }
}
```

- `evidence_map[FID].claim` 指向该 finding 被审查的主论断；
- `evidence_map[FID].items` 是该论断的全部证据，可逐条回到原文复核；
- `relations` 汇总关系计数，快速暴露"引用不支持"型问题。

## 5. 一致性不变量（check / selfcheck 校验）

1. 每条 finding 的 `quote` 与每条 `evidence[].quote` 逐字命中原文；
2. `location` / `evidence[].locator` 与命中段落一致；
3. 六类 facet 全部被覆盖（有 finding 或列入 unchecked_items）；
4. `evidence_map` 的 `evidence_count` 与 `finding.evidence` 长度一致；
5. 重复运行 `findings/counts/coverage` 结构一致，易变量只进 `runtime`。
