# 输出契约：开题报告与研究计划书 result.json 字段规范

本文件定义 `/app/output/result.json` 的字段语义、七要素结构、风险分类词表与
source 结构。SKILL.md 与本文件是执行时的权威依据；case.json 的
`output_contract` 若声明了额外字段，必须**原样回填**，不改名、不删未知键，
缺值给 `null`/`[]`。

## 1. 信封字段（每个 result.json 必须包含）

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 与 case.json 的 `case_id`/`id` 一致；拿不到时填 `"unknown"` 并在 uncertainty 说明 |
| `status` | string | `complete` / `partial` / `insufficient_evidence` / `input_error` / `failed`，如实降级，不编造 |
| `title` | string | 开题方案标题 |
| `summary` | string | 一段摘要，交代七要素口径与边界 |
| `proposal` | object | 七要素结构，见第 2 节 |
| `artifacts` | array | 产物清单，元素为 `{path, format, role}`；路径必须真实存在 |
| `sources` | array | 证据清单，元素结构见第 4 节 |
| `uncertainty` | array | 不确定/待确认/缺口项，元素见第 5 节 |
| `pending_human` | array | 需要人工裁决的项 |
| `change_log` | array | 修改记录，元素为 `{at, action, reason}` |
| `errors` | array | 运行错误记录 |
| `degradations` | array | 降级记录（缺数据/缺软件/超大文件等） |
| `runtime` | object | 运行信息：`init_at`、`budget_seconds`、`budget_requests` 等 |

`output_contract` 声明的键（如 `title`/`summary`/`proposal`）必须逐键回填；
未声明的键按上表作为默认规范补齐。

## 2. 七要素结构（proposal）

评分“结果完整性”要求七要素齐全；缺一即影响完整性与“立得住/对得上/排得开/说得清”。

### 2.1 research_background（研究背景与缺口）

```json
{
  "known": [{"statement": "已知结论（逐字可指认）", "sources": ["S1"]}],
  "unknown": [{"statement": "未知/尚未解决的子问题"}],
  "gap": "缺口陈述：背景材料是否真的指向该缺口，而非文献罗列",
  "gap_sources": ["S2"]
}
```

要求：`known` 是材料中的已知结论，`unknown` 是空白点，`gap` 把两者收敛成一个
“已知—未知—待验证”的缺口，不得写成综述。

### 2.2 research_problems（研究问题）

数组元素：

```json
{"id": "P1", "question": "可回答的问题", "falsifiable_criterion": "可证伪判据",
 "scope": "边界/样本/对象", "sources": ["S1"]}
```

要求：1–3 个，具体、可证伪；每个问题有可考核判据与边界。

### 2.3 objectives（目标与考核标准）

数组元素：

```json
{"id": "O1", "problem_ref": "P1", "objective": "目标",
 "metrics": [{"name": "指标", "threshold": "阈值", "basis": "S3"}],
 "sample": "样本", "control": "对照", "timepoint": "时间点", "sources": ["S3"]}
```

要求：`problem_ref` 必须指向已存在的问题 id（对得上）；指标阈值必须有依据。

### 2.4 technical_route（技术路线）

数组元素：

```json
{"step_id": "S1", "name": "步骤", "input": "输入", "output": "输出",
 "depends_on": ["S0"], "parallel_with": ["S2"], "sources": ["S1"]}
```

要求：每步有输入输出与前后依赖；标注可并行部分；无据连接不画。

### 2.5 phase_plan（阶段计划）

```json
{"total_period": "24个月", "budget": "经费", "personnel": "人员",
 "milestones": [{"id": "M1", "quarter": "Q1", "deliverable": "交付物",
                 "tasks": ["t1"], "resource": "资源"}],
 "critical_path": ["S1", "S2"], "feasibility_note": "可行性说明"}
```

要求：里程碑落在给定周期、经费、数据与人员条件内（排得开）；任务量与里程碑匹配；
标注关键路径。

### 2.6 risks（风险与备选方案）

数组元素：

```json
{"id": "R1", "category": "data_acquisition", "risk": "风险描述",
 "trigger_signal": "触发信号", "impact": "影响范围", "alternative": "备选方案",
 "decision_point": "决策时点", "sources": ["S1"]}
```

要求：按风险分类覆盖（数据获取、方法可行性、进度、伦理与合规至少各检一次）；
每条风险有触发信号、影响、备选方案与决策时点（说得清）。

### 2.7 expected_outcomes（预期成果与考核方式）

数组元素：

```json
{"id": "EO1", "outcome": "成果", "verification_method": "考核方式", "sources": ["S1"]}
```

要求：成果与问题一一对应，并写明考核方式（指标/样本/对照/时间点）。

### 2.8 boundaries（边界）

```json
{"in_scope": ["范围内"], "out_of_scope": ["范围外"]}
```

## 3. 风险分类词表（category 合法值）

`data_acquisition`（数据获取）、`method_feasibility`（方法可行性）、
`schedule`（进度）、`ethics_compliance`（伦理与合规）、`other`。

## 4. source 结构（证据溯源硬要求）

```json
{"id": "S1", "locator": "materials/paper.pdf#p3 或 guide.md#第2段",
 "quote": "逐字原文片段", "kind": "fact|summary|inference",
 "material_kind": "text|binary|other", "note": "可选说明"}
```

- `locator` 与 `quote` 至少其一；关键结论/数值/引用必须同时给 locator + 逐字 quote。
- `kind`：`fact`=原文可逐字指认；`summary`=多段归纳；`inference`=Agent 补出（必须
  同时写入 `uncertainty`）。
- 冲突信息不隐藏：同一指标两个来源不一致时，两者都记录并标 `source_conflict`
  （写入 `uncertainty` 的 `kind`）。

## 5. uncertainty 结构

```json
{"item": "materials", "issue": "缺什么", "kind": "missing|conflict|inference|out_of_scope|oversized|unreadable",
 "action": "建议动作"}
```

`pending_human` 元素同构，用于必须人工裁决的项（如两来源冲突、伦理审批）。

## 6. 契约回填规则（脚本已实现，Agent 复核）

- `init` 读 case.json 的 `output_contract`（兼容 `contract`/`output_schema`/
  `required_fields`），把每个声明的键以 `null`（列表型给 `[]`）写入骨架顶层，
  不覆盖信封字段。
- 契约未声明时使用默认规范字段 `title`/`summary`/`proposal`/`artifacts`。
- 未知键不改名、不删除；缺值不编造。
