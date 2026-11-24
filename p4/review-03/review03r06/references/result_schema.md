# 输出与中间产物契约（traceable-stats-audit）

本文定义 `result.json`、`report.md` 与 `/app/work/` 下中间产物的字段语义。脚本按此校验；
Agent 写 finding/meta 时必须遵守。

## 1. result.json 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 取自 case.json |
| `status` | string | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `summary` | object | 是 | `verdict` + `counts.total` + `by_facet`/`by_type`/`by_severity` |
| `review_report.findings` | array | 是 | 逐条审查意见（§3） |
| `result_status` | object | 是 | `overall`（通过/问题/待复核/未检查）+ `pass`/`issue`/`needs_review`/`unchecked` |
| `evidence.evidence_map` | array | 是 | 定位+摘录+依据+源段哈希（§4） |
| `sources` | array | 是 | 实际核验来源（文件/页/段/表/图），不含虚构 DOI/URL |
| `uncertainty` | array | 是 | 不确定项 |
| `pending_human` | array | 是 | 需人工复核项 |
| `limitations` | array | 是 | 局限与未完成检查项 |
| `errors` / `degradations` | array | 是 | 输入/软件/解析问题 |
| `change_log` | array | 是 | `{time, action, reason}`，记录每次落盘改动 |
| `runtime` | object | 是 | `started`/`seconds`/`requests_estimate` 等 |
| `numeric_checks` | array | 是 | 脚本数值一致性候选（§5） |
| `output_contract` | object | 否 | case.json 中 output_contract 的原始副本 |

若 `case.json.output_contract` 为对象，`scaffold.py` 将其**每个键原样回填到 result.json 顶层**
（缺值 `null`/`[]`/`""`，不改名不删未知键），`finalize.py` 合并时保留所有未知顶层字段。

## 2. plan.json（问题模型，Agent 逐问更新）

```json
{
  "schema_version": "1.0",
  "case_id": "...",
  "facets": [
    {
      "id": "research_design",
      "title": "研究设计",
      "questions": [
        {"qid": "rd_01", "question": "...", "status": "unchecked",
         "finding_ids": [], "note": ""}
      ]
    }
  ],
  "checked_facets": [],
  "unchecked": []
}
```

`question.status` ∈ `unchecked|checked_ok|finding|not_reported|needs_review`。每完成一 facet 就把
该 facet 移入 `checked_facets`；未完成的留在 `unchecked`。五个 facet 均须有终态，否则 finalize
在 `result_status.unchecked` 与 `limitations` 中列出。

## 3. finding（`/app/work/findings/<id>.json`，一个文件一个对象）

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 如 `f001`，稳定唯一 |
| `facet` | 是 | 五 facet 之一 |
| `check_class` | 是 | 六 check_class 之一 |
| `finding_type` | 是 | `factual_error/insufficient_evidence/normative_issue/advisory/pending_review` |
| `severity` | 是 | `critical/major/minor/advisory/pending` |
| `locator` | 是 | 可回查定位串（`p.3 ¶2`、`p.4 Table 1`、`¶2`） |
| `quote` | 是 | 逐字摘录自 segments.json，不拼接非相邻文字 |
| `basis` | 是 | 判断依据（1–3 句，可含方法—数据匹配规则引用） |
| `suggestion` | 是 | 修改建议（作者可执行） |
| `confidence` | 是 | `high/medium/low` |
| `review_status` | 是 | `confirmed/needs_review/not_reported/not_verified` |
| `source_sha` | 是 | `locator+quote` 来源段的 SHA1 前 12 位 |
| `source_locators` | 否 | 冲突时记录两处及以上定位 |
| `source_conflict` | 否 | 布尔，标来源冲突 |

## 4. evidence_map（finalize 自动生成）

每条 finding 生成一条：`{finding_id, locator, quote, source_sha, basis, sources: [...]}`。
`source_sha` 由 `segments.json` 中对应段的规范化文本计算；复核人重跑 discover 后哈希一致，
即证明摘录未被改写。

## 5. numeric_checks（finalize 自动生成，仅候选）

每条：`{id, kind, segment_id, locator, matched_text, detail, status:"candidate"}`。
`kind` ∈ `p_vs_wording` / `p_vs_ci` / `n_inconsistency`。Agent 复核后确证者写为 finding，
并把 `numeric_checks[i].status` 改为 `confirmed`/`rejected`（写进 `meta.json.numeric_decisions`）。

## 6. figure_model.json（discover 自动生成，供交叉核对）

```json
{
  "schema_version": "1.0",
  "figures": [
    {"id": "fig_1", "kind": "figure", "locator": "p.3 Fig.1", "caption": "...",
     "mentions": [{"segment_id": "s00012", "locator": "p.3 ¶2", "text": "..."}]}
  ],
  "tables": []
}
```

用于核对正文引用与图表/图注的数值是否一致；发现不一致时 finding 记两处 locator。

## 7. meta.json（Agent 在阶段 2 填写）

`status/overall_verdict/overall_assessment/checked_facets/sources/uncertainty/limitations/
numeric_decisions/errors/degradations/change_log`。finalize 会把它合并进 result.json 的对应字段，
并据此设置最终 `status` 与 `summary.verdict`。

## 8. report.md（人类可读）

按 facet 分组列出 findings（含定位、逐字 quote、严重度、类型、依据、建议），随后列出未检查项、
数值候选检查、不确定性、局限、来源与 change_log；顶部给 overall verdict 与计数摘要。
