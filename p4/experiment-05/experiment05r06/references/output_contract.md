# 输出契约与中间表示规范

本文件定义本 Skill 运行时产物的字段语义、证据格式与中间表示（intermediate representation）schema。字段名与含义为硬约定：**不改名、不删键**；未知值写 `null` 或 `[]`。

## 1. result.json 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 必填，非空。来源优先级：case.json 的 `case_id`/`id`/`challenge_id`/`armiId` → task.md 的 `challenge_id` → 输入目录名 |
| `status` | string | `ok`（材料充分且五段完整）/`partial`（部分完成）/`insufficient_evidence`（材料不足）/`input_error`（输入无法解析） |
| `task_type` | string | 固定 `experiment_result_interpretation` |
| `summary` | string | 1–3 句平实结论 |
| `interpretation` | object | 五段结论，见第 2 节 |
| `experiment_plan` | object | 实验方案：`hypothesis`/`testable_question`/`variables`/`controls`/`steps`/`parameters`/`resources`/`interpretation_criteria` |
| `evidence_and_risk` | object | `evidence_basis`（文献/设备/历史记录依据）/`risk_and_anomaly_handling`/`assumptions` |
| `confirmation_items` | object | `safety`/`ethics`/`equipment_availability`/`expert_confirmation`；未经确认事项不写成可执行事实 |
| `intermediate_artifacts` | object | 指向中间表示工件（相对路径），见第 3 节 |
| `sources` | array | 使用的来源文件清单，每项含 `path` 与可选 `kind` |
| `uncertainty` | array | 单位/方法/阈值歧义、数据缺口等 |
| `change_log` | array | 每项 `{ts, phase, action}`，记录骨架→画像→模型→渲染→定稿 |
| `errors` / `degradations` | array | 坏 JSON、坏编码、超限跳过等 |
| `limitations` | array | 对照缺失、重复数不足、缺软件等 |
| `pending_human` | array | 需专家确认/无法判断的事项 |
| `runtime` | object | `{started_at, finished_at, seconds}`；`seconds` 由平台记录真实耗时，脚本不臆造 |

若 `case.json` 含 `output_contract`（`fields`/`required`/`schema` 列表或直接键），**原样回填其全部键**：缺值给 `null` 或 `[]`，未知键保留，不改名。

## 2. 五段 `interpretation` 字段

每段为对象数组；每条关键结论必须带证据。证据对象格式：

```json
{"locator": "materials/paper.md#p3", "quote": "……逐字摘录……"}
```

`locator` 形如 `相对路径#片段`，片段可为 `#p3`（段落）、`#L34`（行）、`#row:9`（表格行）、`#col:force_N`（列）。`quote` 必须是从材料中逐字摘录的原文，不得改写。

### 2.1 trends

每项：`series_id`、`source`、`direction`（increasing/decreasing/stable/non_monotonic/inflection）、`interval`、`control`、`fit_method`、`amplitude`、`units`、`claim`、`evidence`。

### 2.2 anomalies

每项：`anomaly_id`、`subject`（`{dimension, locator}`，dimension ∈ sample/batch/channel/well/timepoint/row）、`observed`、`expected`、`deviation`、`method`（z_score/iqr/mad/grubbs/manual）、`threshold`（数值）、`relation_to_controls`、`claim`、`evidence`。

### 2.3 possible_causes

每项：`cause_id`、`hypothesis`、`rank`（按证据强度/可检验性排序，1 为最强）、`supporting_evidence`、`excluding_evidence`、`confound_risk`、`testability`。

### 2.4 verification_steps

每项：`step_id`、`action`、`target`、`expected_if_supported`、`decision_rule`、`evidence`。

### 2.5 followup_experiments

每项：`followup_id`、`change`、`reason`、`expected_outcome`、`evidence`。

## 3. 中间表示（可复核的关键）

结论由中间表示渲染而来，每条结论携带 `model_ref` 回指 model 节点 id。中间表示工件写在 `/app/work/`，最终产物写在 `/app/output/`。

### 3.1 data_profile.json（机器画像）

```json
{
  "case_id": "…", "created_at": "…",
  "files": [{"path": "…", "rel": "…", "size_bytes": 123, "ext": ".csv"}],
  "tables": [
    {
      "path": "…", "delimiter": ",", "has_header": true,
      "n_rows": 197, "n_columns": 3,
      "columns": [
        {"column": "force_N", "index": 2, "n": 197, "numeric_n": 197,
         "stats": {"n":197,"mean":…,"median":…,"stdev":…,"min":…,"max":…,"q1":…,"q3":…,"iqr":…,"mad":…},
         "zscore_outliers": [{"index":42,"value":12.7,"z":4.1}],
         "iqr_outliers": […], "mad_outliers": […]}
      ]
    }
  ],
  "skipped_for_conversion": [{"rel":"…","ext":".xlsx","note":"requires pandas/openpyxl export"}]
}
```

### 3.2 model.json（解释模型）

```json
{
  "case_id": "…", "schema_version": "1.0",
  "series_models": [{"id":"S1","source":{"locator":"…","columns":[…]},"n":197,"units":null,
    "stats":{…},"trend":{"direction":"decreasing","interval":"…","control":null,
    "fit_method":"linear regression","amplitude":"…","units":"N","claim":"…","evidence":[…]},
    "inflection_points":[{"locator":"…","index":42,"value":…,"evidence":"…"}]}],
  "anomaly_model": [{"id":"A1","subject":{"dimension":"sample","locator":"…"},
    "observed":…,"expected":…,"deviation":…,"method":"z_score","threshold":3.0,
    "relation_to_controls":"…","claim":"…","evidence":[…]}],
  "cause_model": [{"id":"C1","hypothesis":"…","rank":1,
    "supporting_evidence":[…],"excluding_evidence":[…],
    "confound_risk":"…","testability":"…"}],
  "verification_model": [{"id":"V1","action":"…","target":"…",
    "expected_if_supported":"…","decision_rule":"…","evidence":[…]}],
  "followup_model": [{"id":"F1","change":"…","reason":"…","expected_outcome":"…","evidence":[…]}],
  "experiment_plan_model": {"experiment_plan":{…},"evidence_and_risk":{…},"confirmation_items":{…}},
  "uncertainty_model": […]
}
```

### 3.3 figure_model.json（声明式图模型）

```json
{
  "schema_version": "1.0",
  "figures": [
    {"id":"fig_S1","title":"…",
     "series":[{"source":{"locator":"…","columns":[…]}, "style":"line"}],
     "axes":{"x":"time_s (s)","y":"force_N (N)"},
     "annotations":[{"target":"A1","type":"marker","locator":"…"}],
     "generated": false}
  ]
}
```

`figure_model` 使图可复现：任何人只需该文件与源数据即可重画，无需重读全部材料。

### 3.4 evidence_map.json（证据映射）

汇总所有 `locator`+`quote`，每项 `{section, item_id, locator, quote}`，便于按来源复核。

## 4. 确定性要求

- 所有列表按稳定键（文件名、索引、id）排序；禁止依赖随机数、字典迭代顺序或系统时间做排序依据。
- 同一输入两次运行，`result.json`/`model.json`/`evidence_map.json` 内容一致（`runtime` 时间戳除外）。
