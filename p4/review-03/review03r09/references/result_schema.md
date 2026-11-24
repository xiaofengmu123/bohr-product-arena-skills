# result.json 输出契约

`/app/output/result.json` 的字段定义与合法值。字段名与 `output_contract`
回填字段冲突时，**回填字段原样保留**，本表只约束本 Skill 自有的标准字段。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识，取自 `/input/case.json`；缺省 `"unknown"` |
| *(output_contract 字段)* | any | `scaffold` 原样回填，不改名、不删未知键、缺值给 `null`/`[]`/`""` |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `summary` | object | 见 §2 |
| `review_report` | object | `{ "findings": [...] }`，finding 见 §3 |
| `result_status` | string | `通过` / `问题` / `待复核` / `未检查` |
| `evidence` | object | `{ "evidence_map": {finding_id: {source, locator, quote}} }` |
| `sources` | array | 实际核验来源（文件相对路径、材料页号）；不写虚构 DOI/URL |
| `uncertainty` | array | 无法确认、需外查或存在冲突的事项 |
| `pending_human` | array | 需人工裁决的条目 |
| `limitations` | array | 未覆盖检查面、未完成项、材料缺口 |
| `errors` | array | 运行/解析错误 |
| `degradations` | array | 降级处理记录（缺工具、扫描件、坏编码等） |
| `change_log` | array | `{at, action, detail}` 增量变更记录 |
| `runtime` | object | `{started_at, finished_at, seconds}`（时间戳为唯一非确定字段） |
| `numeric_checks` | array | 脚本候选检查结果，§4 |

## 2. summary 结构

```json
{
  "verdict": "通过|有问题|材料不足|待复核|未检查",
  "overall_assessment": "一段对稿件总体方法学质量的诚实评价",
  "counts": {
    "total": 0,
    "factual_error": 0,
    "insufficient_evidence": 0,
    "normative_issue": 0,
    "advisory": 0,
    "pending_review": 0,
    "critical": 0,
    "major": 0,
    "minor": 0
  },
  "by_facet": {"research_design": 0, "variable_definition": 0,
               "statistical_methods": 0, "sample_size_power": 0,
               "results_reporting": 0},
  "checked_facets": ["..."],
  "unchecked": ["..."]
}
```

## 3. finding 对象

必填六项：`id`、`facet`、`locator`、`quote`、`severity`、`suggestion`。
推荐补齐：`finding_type`、`check_class`、`confidence`、`review_status`、
`source`、`basis`、`related_locators`。

| 字段 | 取值/说明 |
|---|---|
| `id` | 稳定短 id，如 `RD-01`、`VM-02`、`SM-03`、`SS-04`、`RR-05` |
| `facet` | 五个检查面之一（见 checklist §1） |
| `check_class` | 六类审查面之一（见 checklist §2） |
| `finding_type` | `factual_error` / `insufficient_evidence` / `normative_issue` / `advisory` / `pending_review` |
| `severity` | `critical` / `major` / `minor` / `advisory` / `pending` |
| `confidence` | `high` / `medium` / `low` |
| `review_status` | `confirmed` / `needs_review` / `source_conflict` |
| `locator` | 可回查定位串，如 `p.3 ¶2`、`p.4 Table 1`、`¶2` |
| `quote` | 逐字摘录，来自 `/app/work/texts/*.txt` |
| `source` | 材料文件相对路径 |
| `basis` | 判断依据；未取得来源写 `not_verified` |
| `suggestion` | 面向作者的具体修改建议 |
| `related_locators` | 冲突处第二证据等其它定位串 |

## 4. numeric_checks 条目

脚本只输出候选，须人工确认后提升为 finding：

```json
{
  "id": "NC-1",
  "rule": "p_vs_wording | p_vs_ci | n_inconsistency",
  "locator": "p.5 ¶1",
  "source": "paper.pdf",
  "detail": "候选描述",
  "evidence": {"p": "0.04", "ci": "0.8, 1.4", "snippet": "原文片段"}
}
```

## 5. 完整性自检要点

- `case_id` 非空；`status` 与 `result_status` 均为合法值。
- `output_contract` 全部键存在于 result.json 顶层（若输入提供了契约）。
- 每条 finding 六必填非空；`quote` 与 `locator` 能回查原文。
- `summary.counts` 与 findings 实际分类一致（finalize 自动重算）。
- `sources` 只含实际读取的文件；`change_log` 记录了 scaffold/finalize 等关键动作。
