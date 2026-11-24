# result.json 字段规范

本文件是 `/app/output/result.json` 的字段规范，也是 `scripts/main.py assemble` 的
实现契约。所有字段由确定性脚本从 `/app/work/state.json` 计算得出，人工只改
state，不改产物，从而保证「输入可解析、输出可复核、重复运行一致」。

## 1. 顶层字段（`*` = 强制）

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id`* | string/null | 取自输入的案例标识；缺失给 null |
| `status`* | enum | `ok` / `partial` / `insufficient_evidence` / `input_error`，按覆盖与停止判断推导 |
| `research_question` | string/null | 原问题全文（不做改写） |
| `sub_questions`* | array | 六要素拆解，见 §2 |
| `query_plan`* | object | 查询规划，见 §3 |
| `term_expansion`* | object | 术语扩展，见 §3 |
| `results`* | array | 排序候选集，见 §4 |
| `screening`* | object | 全量筛选账本，见 §5 |
| `duplicates`* | array | 重复导出合并，见 §6 |
| `conflicts`* | array | 证据冲突，见 §6 |
| `coverage`* | object | 覆盖矩阵与范围声明，见 §7 |
| `evidence`* | array | 查询/筛选/冲突/缺口日志 |
| `sources`* | array | 按数据库聚合的来源清单 |
| `uncertainty`* | object | 缺失元数据/冲突/覆盖缺口 |
| `stop_decision`* | object | 停止判断（status/reason/criteria_met/criteria_unmet/next_steps） |
| `change_log`* | array | `{time, action, reason, status}` 增量历史 |
| `status_notes` | string/null | 状态补充说明 |
| `limitations` | array | 显式局限（缺数据/缺软件/权限/口径） |
| `errors` | array | 运行期错误 |
| `degradations` | array | 降级（坏编码/坏 JSON/超大文件/接口异常） |
| `pending_human` | array | 需人工补充的具体缺口 |
| `runtime` | object | `{seconds, requests, generated_at, skill_version}` |
| `_notes` | string/null | 内部备注（非评分字段） |
| `<output_contract.*>` | 原样 | 案例 contract 的全部字段，见 §8 |

## 2. sub_questions（六要素拆解）

每条为一个对象，`id` 形如 `SQ1..SQn`：

```json
{
  "id": "SQ1",
  "question": "X 对 Y 的抑制效应是否成立？",
  "object": "化合物 X",
  "mechanism": "Y 通路",
  "method": "剂量-反应",
  "time_range": "2015–2025",
  "comparison": "相对基线 W",
  "evidence_type": "定量数据"
}
```

六要素不全时留空字符串，不要编造。`assemble` 用 `sub_question_ids` 把记录映射回
子问题，形成覆盖矩阵。

## 3. query_plan 与 term_expansion

```json
{
  "query_plan": {
    "steps": [
      {"step": "Q1", "query": "X Y inhibition dose-response",
       "sub_question_ids": ["SQ1"], "depends_on": [], "parallel_with": ["Q2"],
       "rationale": "前置：先查直接证据"}
    ]
  },
  "term_expansion": {
    "concepts": [
      {"concept": "化合物 X", "variants": ["X", "X-1", "X (CAS 000-00-0)"],
       "language_variants": ["中文名"], "scope_note": "每概念 ≤6 变体"}
    ]
  }
}
```

## 4. results（候选集，含排序/筛选依据）

`assemble` 将 included 与 uncertain 记录合并排序（score 降序 → year 降序 → title），
并回填 `rank`；excluded 只进筛选账本，不进 results。每条必含：

```json
{
  "rank": 1,
  "record_id": "P1",
  "title": "...", "authors": ["..."], "year": "2020", "journal": "...",
  "doi": "10....", "url": "...", "database": "materials",
  "sub_question_ids": ["SQ1"],
  "relevance": 0.95, "relevance_reason": "...",
  "screening": "included", "screening_reason": "...",
  "evidence": [{"locator": "materials/paperA.pdf#p3", "quote": "逐字片段"}],
  "score": 0.91,
  "score_breakdown": {"relevance": 0.95, "metadata_completeness": 0.86,
                      "source_reliability": 1.0,
                      "weights": {"relevance": 0.6, "metadata_completeness": 0.2,
                                  "source_reliability": 0.2}}
}
```

`evidence.locator` 与 `evidence.quote` 二者缺一不可：quote 必须逐字取自来源，不允许
转述。缺来源文本时 `quote` 置 null 并写入 `uncertainty.missing_metadata`。

## 5. screening（全量账本，排除项不静默丢弃）

```json
{
  "total_records_seen": 9,
  "included": ["P1", "P2"],
  "excluded": [{"record_id": "P5", "title": "...", "reason": "不涉及 X/Y"}],
  "uncertain": [{"record_id": "P7", "title": "...", "reason": "仅摘要可见"}],
  "merged_duplicates": [
    {"kept_record_id": "P1", "merged_record_id": "P1dup",
     "fingerprint": "doi:10....", "reason": "identical export/duplicate"}
  ]
}
```

## 6. duplicates 与 conflicts（本版新增，应对固定快照中的 fixture）

- `duplicates`：DOI 优先、否则规范化标题指纹，记录「哪个并入哪个」及理由；原记录
  不删除，`merged_record_id` 仍留在账本。
- `conflicts`：当同一指标两个来源不一致，或论文观点冲突时，双方逐字保留：

```json
{
  "record_id": "P3", "title": "...",
  "conflict": {"with": ["P4"], "issue": "效应方向相反",
               "resolved": "unresolved", "note": "双方均为输入 fixture，暂不取舍"}
}
```

`resolved` 取值：`unresolved` / `reconciled` / `preferred`。

## 7. coverage（有限快照覆盖模型）

```json
{
  "per_sub_question": {"SQ1": {"covered": true, "included_record_ids": ["P1"],
                              "evidence_count": 1}},
  "unassigned_included_record_ids": [],
  "coverage_scope": "finite snapshot: coverage is over the provided/retrieved record set only; no global-recall claim is made"
}
```

覆盖只对**已提供/已检索到的记录集**统计，不宣称对全网/数据库的全局召回；这是诚实
边界，缺失项进 `uncertainty.coverage_gaps`。

## 8. output_contract 原样回填

案例 `case.json` 中的 `output_contract`（或 `output`/`schema`/`required_fields`/
`contract`）逐键回填到 result.json 顶层：不改名、不删未知键、不添加解释。未获得的
字段给 `null` 或 `[]`。脚本在 `assemble` 阶段用当前输入重新读取该块，保证与案例一致。

## 9. evidence 日志条目

```json
{"id": "E1", "kind": "query|screen|sort|gap|conflict|note",
 "query": "...", "source": "...", "database": "...",
 "timestamp": "2026-01-01T00:00:00Z", "round": 1, "rationale": "..."}
```

每条查询、每轮筛选、排序依据、冲突发现与停止理由都应有对应条目，供独立复核。
