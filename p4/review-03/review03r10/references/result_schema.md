# result.json 输出契约（研究方法与统计审查）

本文说明 `/app/output/result.json` 的字段。机器校验由 `scripts/core.py` 的
`run_selftest` 执行，校验规则与本文一致。除下表“引擎管理字段”外，`finalize` 保留
`result.json` 中任何未知顶层字段；`output_contract` 的全部键原样铺到顶层并同时在
`output_contract` 下保留逐字副本。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 案例标识，取自 `/input/case.json` 的 `case_id`/`id`/`caseId` |
| `status` | enum | 是 | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，须诚实 |
| `summary` | object | 是 | `verdict`（通过/问题/待复核/未检查/材料不足/输入错误）+ `counts` + `by_facet` + `by_type` |
| `review_report` | object | 是 | `findings[]` + `coverage`（checked_facets/unchecked_facets/check_classes） |
| `result_status` | enum | 是 | 通过/问题/待复核/未检查 |
| `evidence` | object | 是 | `evidence_map`（逐条 locator+quote）+ `segments_index` + `numeric_checks[]` |
| `output_contract` | object | 条件 | 若 case.json 提供，逐字保留 |
| `sources` | array | 是 | 只含实际核验来源 |
| `uncertainty` | array | 是 | 不确定项 |
| `pending_human` | array | 是 | 待人工复核项 |
| `limitations` | array | 是 | 局限与未完成检查项 |
| `errors` | array | 是 | 输入/解析错误 |
| `degradations` | array | 是 | 降级记录（缺软件、扫描件等） |
| `change_log` | array | 是 | `[{at, event}]`，每次变更追加 |
| `runtime` | object | 是 | `started_at/updated_at` |

## 2. finding 对象（review_report.findings[]）

| 字段 | 必填 | 取值 | 说明 |
|---|---|---|---|
| `id` | 是 | `F001` 形式 | 稳定唯一，脚本自动补缺 |
| `facet` | 是 | 见 checklist §1 | 五个检查面之一 |
| `locator` | 是 | `p.3 ¶2` / `¶2` / `p.4 Table 1` | 可回查定位串 |
| `quote` | 是 | 逐字摘录 | 必须与 `/app/work/texts/*.txt` 一致 |
| `severity` | 是 | critical/major/minor/advisory/pending | 与缺陷性质匹配 |
| `suggestion` | 是 | string | 可执行的修改建议 |
| `finding_type` | 是 | factual_error/insufficient_evidence/normative_issue/advisory/pending_review | 五类区分 |
| `check_class` | 是 | 见 checklist §2 | 六类核对面之一 |
| `confidence` | 是 | high/medium/low | 判断置信度 |
| `review_status` | 是 | confirmed/needs_review | 无法确认标 needs_review |
| `basis` | 否 | string | 判断依据；未取得来源写 `not_verified` |

## 3. 状态取值约定

- 有材料、完成审查且结论可信 → `ok`；
- 已产出部分可用结论但仍有未检查/待复核项 → `partial`；
- 无材料或材料不足以判断 → `insufficient_evidence`；
- 缺 case.json 且无材料、输入无法解析 → `input_error`。

`status` 与 `summary.verdict`、`result_status` 三者保持一致口径：任何“缺失”写
`not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源或结论。

## 4. 工作目录中间产物（/app/work）

| 文件 | 产生于 | 用途 |
|---|---|---|
| `state.json` | scaffold/commit/finalize | 阶段、序号、case_id、contract_keys |
| `ledger.json` | probe | 材料清单、segments、term_hits、numeric_checks 候选 |
| `segments.json` | probe | 定位索引（id/locator/text） |
| `texts/*.txt` | probe | 抽取的原文，供逐字摘录 |
| `findings/*.json` | 模型写入 | 每条 finding 一个对象（或一个数组），由 commit 摄取 |
| `meta.json` | 模型写入 | verdict/status/checked_facets/sources/uncertainty/limitations 等 |
| `numeric_decisions.json` | 模型写入 | 数值候选复核结论（promote/discard/pending） |
