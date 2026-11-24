# result.json 输出契约与中间产物说明

## 1. 主产物

`/app/output/result.json`（机器可复核）与 `/app/output/report.md`（人类可读、可编辑）。
result.json 可被 `json.load` 解析，键排序稳定、无随机内容。

### 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识，取自 case.json 的 `case_id`/`id`/`caseId` |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error`，须诚实 |
| `summary` | object | `verdict` + `counts` + `facet_counts` + `check_class_counts` + `finding_type_counts` |
| `review_report` | object | `findings`（逐条问题）+ `coverage`（已检查/未检查 facet、已覆盖 check_class） |
| `result_status` | object | `overall`（通过/问题/待复核/未检查）+ `by_facet` + `unchecked` |
| `evidence` | object | `evidence_map`（每条 finding 的定位+摘录）+ `segments_ref` |
| `sources` | array | 只含实际核验来源；禁止虚构 DOI/URL/数值 |
| `uncertainty` | array | 无法确认/冲突事项 |
| `pending_human` | array | 需人工复核事项 |
| `limitations` | array | 未完成检查项、材料缺口 |
| `errors` | array | 解析/读取错误 |
| `degradations` | array | 降级记录（缺 pdftotext、扫描版、坏编码等） |
| `change_log` | array | `{at, action, detail}` 变更记录 |
| `runtime` | object | `start_epoch/start_at/end_epoch/end_at` |
| `numeric_checks` | array | 脚本自动候选检查（`{type, locators, detail, status:"candidate"}`） |
| 其余顶层键 | any | `output_contract` 的全部字段**原样回填**（不改名、不删未知键） |

### output_contract 回填规则

- `scaffold` 读取 `/input/case.json` 的 `output_contract`，把每个键**原样写入 result.json 顶层**。
- 缺值给 `null`/`[]`/`""`；不改名、不删未知键。
- `finalize` 从既有 result.json 出发只更新已知字段，**保留所有未知顶层字段**。

## 2. finding 对象（每条审查意见）

```json
{
  "id": "F-001",
  "facet": "statistical_methods",
  "check_class": "method_design",
  "finding_type": "factual_error",
  "severity": "major",
  "locator": "p.3 ¶2",
  "quote": "（逐字摘录原文）",
  "basis": "（判断依据）",
  "suggestion": "（修改建议）",
  "confidence": "high",
  "review_status": "checked",
  "validation": {
    "locator_resolves": true,
    "quote_verbatim": true,
    "errors": []
  }
}
```

必填：`id / facet / locator / quote / severity / suggestion / finding_type /
check_class / confidence / review_status`。`basis` 建议填写；`validation` 由
`finalize` 自动追加（回查 locator 是否存在于 segments、quote 是否逐字子串匹配）。

合法值：`facet` ∈ 五个检查面；`check_class` ∈ 六个审查面；`finding_type` ∈ 五类缺陷；
`severity` ∈ `critical|major|minor|advisory|pending`；`review_status` ∈
`checked|needs_review`；`confidence` ∈ `high|medium|low`。

## 3. 中间产物（/app/work）

| 文件 | 内容 |
|---|---|
| `manifest.json` | case_id、has_case、has_output_contract、材料清单、errors/degradations |
| `segments.json` | `{segments:[{id,file,locator,page,para,text}]}` 定位索引 |
| `terms.json` | 统计关键词命中（term/file/locator/context） |
| `meta.json` | 状态、verdict、checked_facets、sources/uncertainty/limitations 等 |
| `state.json` | scaffold 阶段状态 |
| `findings/*.json` | 一条意见一个文件（一个对象），由 Agent 逐条写入 |

## 4. report.md 格式

依次为：标题与 case_id/status/verdict/意见计数 → 检查面覆盖 → 审查意见（按 facet 分组，
每条含 locator、类型、严重度、逐字摘录、依据、建议）→ 数值一致性候选 → 局限性/不确定性/
待复核/错误/降级 → 变更记录。
