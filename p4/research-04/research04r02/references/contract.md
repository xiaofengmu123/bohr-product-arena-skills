# 输出契约与字段字典（contract.md）

本文件是 `result.json` 的权威字段定义；脚本与 SKILL.md 以本文件为准。
字段命名与 `challenge.json` / 案例 `output_contract` 冲突时，**output_contract 优先**，
本文件固定字段只做补充、不覆盖。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 取 case.json 的 `case_id`/`id`/`challenge_id`/`caseId`，再取 task.md 的 `challenge_id`，最后 `"unknown"` |
| `status` | enum | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，诚实反映完成度 |
| `_meta` | object | `{skill, version, revision, machine}`；`machine=true` 表示该版含未核验的机械候选 |
| `_baseline` | object | discover/baseline 阶段的原始机械抽取（可复算的中间证据，不删除） |
| `research_question` | object | `{question, scope, sub_questions[], domain_note}` |
| `protocol` | object | `{search_scope, inclusion_criteria[], exclusion_criteria[], date_definition_note, dedup_note}` |
| `time_trend` | object | 见下 |
| `topic_evolution` | object | 见下 |
| `representative_works` | object | 见下 |
| `research_gaps` | object | 见下 |
| `evidence_map` | array | 见下 |
| `conflicts` | array | 见下 |
| `sources` | array | `{id, path, title, year, type, extracted}` |
| `uncertainty` | array[string] | 归并口径、置信度边界等不确定项 |
| `pending_human` | array[string] | 必须人工核验的环节 |
| `change_log` | array | `{time, action, reason, before, after}`，每次写盘一条 |
| `limitations` | array[string] | 缺失材料、未核验类别、解析失败 |
| `errors` | array | 输入/脚本错误，有值即说明 |
| `degradations` | array | 采样、截断、降级记录 |
| `runtime` | object | `{started_at, finished_at, elapsed_s, requests_used, phases[]}` |

## 2. 四类可检查产物

### 2.1 time_trend
```json
{
  "unit_note": "年份口径：在线发表 / 见刊 / 预印本，及离散波动与拐点的判别",
  "distribution": [{"year": 2021, "count": 3, "doc_ids": ["mat_001"]}],
  "windows": [{"label": "早中晚期", "start": null, "end": null, "basis": "", "count": 0}],
  "claims": [{"claim": "…", "support": "reported|inferred|not_reported", "evidence_ids": ["E001"]}]
}
```
- `support` 枚举同 `evidence_map.kind`；趋势判断必须由 `distribution` 支撑，禁止把关键词热度当趋势。

### 2.2 topic_evolution
```json
{
  "stage_divisions": [{"stage": "…", "period": "…", "basis": "…"}],
  "term_mapping": [{"canonical": "…", "variants": ["…"], "first_year": 2019, "last_year": 2023, "note": ""}],
  "events": [{"type": "emergence|merge|split|decline|terminology_drift", "term": "…", "period": "…", "cause": "…", "evidence_ids": []}],
  "claims": [{"claim": "…", "support": "…", "evidence_ids": []}]
}
```
- 每个事件必须写清**何时、因何**；术语换说法归入 `terminology_drift`，同名异义在 `term_mapping.note` 标注。

### 2.3 representative_works
```json
{
  "criteria": ["覆盖各阶段", "含方法首创", "不只按总被引排序"],
  "stages": [{"stage": "…", "period": "…", "works": [
    {"doc_id": "mat_001", "title": "…", "year": 2019,
     "kind": "origin|method|milestone|benchmark", "rationale": "…", "evidence_ids": []}
  ]}]
}
```
- `kind` 必须覆盖 `origin`（长尾方法首创），不能只有 `milestone`（高被引）。

### 2.4 research_gaps
```json
{
  "gaps": [{"id": "G1", "type": "open_question|method_failure|unresolved_conflict|coverage_absence",
            "description": "…", "exclusion_evidence": "检索式 + 排除条件 + 反例排查", "evidence_ids": []}],
  "excluded_not_gaps": [{"candidate": "…", "reason": "…"}]
}
```
- `coverage_absence` = 检索遗漏/材料不足，**不是**可断言的空白；`open_question` 等才是有排除证据支撑的空白。

## 3. evidence_map 与回链

```json
{"id": "E001", "claim": "…", "locator": "materials/mat_001.txt#L12-L15",
 "quote": "逐字引用原文", "source_file": "materials/mat_001.txt",
 "kind": "reported|not_reported|inferred|source_conflict", "confidence": "high|medium|low"}
```
- `quote` 必须为原文逐字片段，`selftest.py` 会做子串定位校验；无法定位时置 `kind=not_reported` 或改写为精确引用。
- 每个关键结论（trend/evolution/gap claim）的 `evidence_ids` 必须指向存在的条目。

## 4. conflicts

```json
{"items": ["来源A：…", "来源B：…"], "resolution": "…或 未决"}
```
- 冲突证据两方都记录，不静默择一；无法裁决写 `resolution="未决"` 并列入 `uncertainty`。

## 5. 质量门槛（判定 status）

- `ok`：四类产物齐全 + 每个关键 claim 有证据回链 + 无未核验 `machine=true` 候选。
- `partial`：产物可打开但不完整，或部分结论待人工复核。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注。
- `input_error`：输入契约不可解析且无法恢复。

## 6. 可编辑伴生产物

| 文件 | 内容 |
|-|-|
| `report.md` | 综述正文（可编辑 Markdown） |
| `evidence_map.json` | 证据映射独立副本 |
| `tables/documents.csv` | 文献清单（doc_id/path/title/year/type） |
| `tables/year_distribution.csv` | 年度分布（year/count/doc_ids） |
| `work/baseline.json` | 机械抽取中间结果 |
| `work/manifest.json` | 输入发现清单 |
| `work/budget.json` | 预算账本（阶段闸门） |
| `work/selftest.json` | 自检逐项结果 |

## 7. 预算账本（本版核心）

`work/budget.json` 是**阶段闸门账本**，Agent 每步开始前先读它决定下一步：

```json
{
  "total_s": 900, "total_requests": 64, "tokens_per_response": 16384,
  "phases": [
    {"id": "discover", "deadline_s": 180, "max_requests": 4, "status": "pending", "requests_used": 0},
    {"id": "baseline", "deadline_s": 180, "max_requests": 6, "status": "pending", "requests_used": 0},
    {"id": "deep_analysis", "deadline_s": 360, "max_requests": 40, "status": "pending", "requests_used": 0},
    {"id": "selfcheck", "deadline_s": 100, "max_requests": 8, "status": "pending", "requests_used": 0},
    {"id": "finalize", "deadline_s": 80, "max_requests": 6, "status": "pending", "requests_used": 0}
  ],
  "requests_used": 0
}
```

- 前 180 秒（discover）**必须**先落盘一版 `result.json` 骨架；此后所有改进都是增量原子写盘。
- 阶段顺序固定：`discover → baseline → deep_analysis → selfcheck → finalize`；
  任一阶段中断都不产生空白产物，`result.json` 始终是合法 JSON。
