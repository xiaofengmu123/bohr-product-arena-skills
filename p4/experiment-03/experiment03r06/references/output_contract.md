# 输出契约（output_contract）

本文件定义 `/app/work/` 下中间表示、`/app/output/plan.json` 与 `/app/output/result.json` 的全部字段。字段名不可改；缺值用 `null`/`[]`/`""` 回填，不删未知键。所有中间表示含 `schema_version` 与 `case_id`。

## 1. 通用取值

- `status`：`ok | partial | insufficient_evidence | input_error`
  - `ok`：可行域、指标、搜索、成本四项可落地且关键值有证据。
  - `partial`：有可执行骨架但部分值未证实/未收敛。
  - `insufficient_evidence`：材料不足以确定参数/响应/约束。
  - `input_error`：输入不可读或坏 JSON 且无可用材料。
- 每条 `sources` 项必须含 `locator`（材料位置，如 `case.json#equipment.max_temp` 或 `materials/xxx.pdf#p3`）与 `quote`（**逐字**引文，非转述）。
- 冲突证据不隐藏：同一指标两个来源不一致时都记录并标 `source_conflict`。

## 2. 中间表示（`/app/work/`）

### input_digest.json（`init` 生成）

```json
{"input_dir": "/input", "files": [{"path":"case.json","size":123,"kind":"text","readable":true,"preview":"..."}],
 "case_id": "...", "case_json_error": null, "task_md_preview": "..."}
```

### constraint_model.json（可行域模型）

```json
{"schema_version":"1.0","case_id":"...",
 "feasibility":{"status":"feasible|conflict|insufficient_evidence|unverified","violations":[{"param":"T","reason":"..."}]},
 "parameters":[{"id":"T","name":"温度","unit":"°C","recommended_value":80,
   "domain":{"min":60,"max":120,"step":5},
   "hard_bounds":[{"kind":"equipment|material|safety","min":20,"max":150,"locator":"...","quote":"..."}],
   "constraints":[{"type":"leq|geq|between|mutex","rhs":"...","locator":"...","quote":"..."}],
   "status":"feasible|unverified|conflict","conflict_note":""}],
 "constraints":[{"id":"c1","type":"leq","lhs":"T","rhs":120,"reason":"...","locator":"...","quote":"..."}]}
```

### response_model.json（响应指标与预测模型，figure_model 对应物）

```json
{"schema_version":"1.0","case_id":"...",
 "metrics":[{"id":"yield","name":"转化率","unit":"%","direction":"maximize|minimize|target",
   "measurement_method":"...","target":85,"acceptable_min":70,"uncertainty":"±5 %",
   "prediction":{"method":"经验规则|历史拟合|代理模型","confidence":"low|medium|high","basis":[{"locator":"...","quote":"..."}]}}],
 "relations":[{"from":"T","to":"yield","direction":"positive|negative|nonmonotonic","note":"...","basis":[...]}],
 "model_note":"..."}
```

### search_model.json（搜索策略与候选点）

```json
{"schema_version":"1.0","case_id":"...","method":"grid|sequential|bayes|staged",
 "initial_points":[{"T":80}],"per_round":"...","stop_criteria":["..."],"candidate_count":8,"seed":0,
 "reproducible":true,
 "candidates":[{"id":"c1","round":1,"point":{"T":80},"rationale":"推荐中心点"}]}
```

### evidence_map.json（证据映射，全链证据根）

```json
{"schema_version":"1.0","case_id":"...",
 "sources":[{"id":"s1","kind":"constraint|metric|basis|cost","locator":"case.json#equipment.max_temp",
   "quote":"...","claims":["温度上限"],"verified":true,"source_conflict":false}]}
```

### tradeoff_model.json（成本/时间/多目标取舍）

```json
{"schema_version":"1.0","case_id":"...",
 "budget":{"currency":"CNY","total":null,"time_hours":null},
 "cost_items":[{"name":"机时","unit":"h","amount":4,"unit_cost":100,"total":400,"locator":"...","quote":"..."}],
 "total":"...","time_hours":8,"tradeoff_note":"...","acceptance_criteria":"..."}
```

### trace.json（溯源链）

```json
{"schema_version":"1.0","case_id":"...",
 "events":[{"at":"2026-11-23T00:00:00Z","stage":"A|B|C|D","action":"...","files":["constraint_model.json"]}]}
```

## 3. plan.json（主结果结构化规格）

顶层 = `PLAN_META_KEYS` ∪ `PLAN_CONTENT_KEYS`：

- 元信息：`title`、`sources`、`uncertainty`、`limitations`、`pending_human`、`change_log`、`case_id`、`status`。
- 主体：`research_question`、`objective`、`assumptions`、`variables`、`parameters`、`controls`、`procedures`、`resources`、`response_metrics`、`interpretation_criteria`、`search_strategy`、`cost_budget`、`basis`、`risks`、`exception_handling`、`confirmations`。

字段约定：

- `parameters`：数组，每项含 `id/name/unit/recommended_value/domain{min,max,step}/hard_bounds/source{locator,quote}/status`。
- `response_metrics`：数组，每项含 `id/name/unit/direction/measurement_method/target/acceptable_min/uncertainty/prediction{method,confidence}`。
- `search_strategy`：`method/initial_points/per_round/stop_criteria/candidate_count/seed/candidates`。
- `cost_budget`：`items[{name,unit,amount,unit_cost,total}]/total/time_hours/tradeoff_note`。
- `sources`：数组，每项含 `locator/quote/claims/kind`。
- `uncertainty`：`{unverified_values[], missing_data[], assumptions[], prediction_uncertainty[]}`。
- `change_log`：数组，每项 `{at, stage, message}`。

## 4. result.json（最终交付）

```json
{
  "case_id": "...",
  "status": "ok|partial|insufficient_evidence|input_error",
  "title": "...",
  "plan": { /* §3 的全部字段 */ },
  "artifacts": {"plan.md": "/app/output/plan.md", "plan.json": "/app/output/plan.json"},
  "sources": [ /* 与 plan.sources 一致 */ ],
  "uncertainty": { /* 同 plan.uncertainty */ },
  "limitations": [],
  "pending_human": [],
  "change_log": [ /* 含首条"骨架落盘" */ ],
  "errors": [],
  "degradations": [],
  "runtime": {"started_at":"...","finished_at":"...","requests_used":null,"seconds_used":null},
  "intermediate_representations": {
    "input_digest": "/app/work/input_digest.json",
    "constraint_model": "/app/work/constraint_model.json",
    "response_model": "/app/work/response_model.json",
    "search_model": "/app/work/search_model.json",
    "evidence_map": "/app/work/evidence_map.json",
    "tradeoff_model": "/app/work/tradeoff_model.json",
    "trace": "/app/work/trace.json"
  }
}
```

- `artifacts` 内路径必须真实存在；`intermediate_representations` 列出中间表示路径，构成可复核的溯源链。
- `runtime`：平台记录模型/时间；脚本只回填 `started_at/finished_at`，不编造请求数。
