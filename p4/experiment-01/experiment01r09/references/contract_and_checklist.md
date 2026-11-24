# 契约、字段语义与自检清单

本文是 `SKILL.md` 的细则：元素字段、证据规范、校验规则与失败修复。脚本只负责塑形与校验，**内容必须由 Agent 依据 `/input` 材料填写**。

## 1. 输出契约与信封

`result.json` 顶层 = 「题目 output_contract 的全部键（逐键镜像）」+ 「信封」。有契约时：契约的每个键原样保留并镜像（缺值 `null`/`[]`，未知键不删不改名），信封键再叠加；无契约时使用下方 fallback 结构。

信封字段：

| 字段 | 类型 | 语义 |
|-|-|-|
| `case_id` | string | 案例标识，取 case.json 的 `case_id/id/challenge_id`，缺失取 task.md `challenge_id`，仍无 `"unknown"` |
| `status` | enum | `ok` / `partial` / `insufficient_evidence` / `input_error`，如实 |
| `artifacts` | object | 产物清单，如 `{"plan_md":"plan.md","result_json":"result.json"}` |
| `sources` | array | 实际读取的材料清单（路径 + 是否提取成功） |
| `uncertainty` | array | 冲突双方、缺口、可接受范围与影响 |
| `pending_human` | array | 需安全/伦理/设备/专家确认的事项，未确认不得写成事实 |
| `limitations` | array | 明确局限（材料不足、无真实报价、无审批等） |
| `degradations` | array | 降级记录（pdftotext 缺失、大文件跳过、坏 JSON 等） |
| `errors` | array | 运行期错误 |
| `change_log` | array | `{ts, event, detail}` 每次大改动追加 |
| `runtime` | object | `skill_version`、`generated_at`、执行命令 |

## 2. Fallback 结构（无 output_contract 时）

```json
{
  "plan": {
    "objective": {"restated": null, "decision_needed": null, "scope_boundary": null},
    "hypothesis": {"h0": null, "h1": null, "decision_rule": null, "alpha": null, "power": null, "expected_effect": null},
    "variables": [],
    "controls": [],
    "design": {"n_per_group": null, "n_groups": null, "n_total": null, "randomization": null, "blinding": null, "replicates": null},
    "steps": [],
    "parameters": [],
    "resources": {"equipment": [], "materials": [], "budget": {"currency": null, "cap": null, "lines": [], "total": null}, "bookings": []},
    "schedule": {"chain": [], "parallel_groups": [], "total_elapsed_min": null, "calendar_days": null},
    "readout": {"primary_metric": null, "unit": null, "protocol": null, "gold_standard": null, "success_criterion": null, "failure_criterion": null},
    "candidates": [],
    "selected_candidate": null,
    "revisions": []
  },
  "evidence_and_risks": {
    "evidence": [], "source_conflicts": [], "risks": [], "assumptions": [], "feasibility_checks": []
  },
  "confirmations": {
    "safety": [], "ethics": [], "equipment_availability": [], "expert_signoff": [], "open_questions": []
  }
}
```

## 3. 六要素字段语义

### 3.1 假设与变量

`hypothesis`：H0/H1 必须是**可检验陈述**，配 `decision_rule`（用什么统计量、什么阈值判成败）、`alpha`、`power`、`expected_effect`。拒绝「抑制剂有影响」这类无操作定义的复述。

`variables[]` 每项：

```json
{"name":"", "role":"independent|dependent|confounder|covariate",
 "operational_definition":"", "measurement":"", "unit":"",
 "range_min":null, "range_max":null, "levels":[], "confounders_controlled":[]}
```

要点：自变量给水平/梯度；因变量给测量方式与单位；混杂因素写明用什么方法控制（随机、匹配、固定、分层）。

### 3.2 对照组

`controls[]` 每项：

```json
{"id":"", "type":"blank|negative|positive|dose_gradient|sham",
 "purpose":"", "excludes_confound":"", "n":null, "matched_on":[]}
```

每个对照必须写「排除哪一类混杂」，避免只有空白组而无阳性/阴性对照；说明与目标的对应关系。

### 3.3 设备材料

`resources.equipment[]`：`{id, model, spec, range:[lo,hi], unit, replaceable:bool, alternative}`；`resources.materials[]`：`{name, spec, supplier, amount, unit, replaceable:bool, alternative}`。设备型号与量程必须来自材料/合同，不得编造；不可替代项显式标出。

### 3.4 时间成本

`resources.budget`：`{currency, cap, lines:[{item, qty, unit_price, basis_locator}], total}`，`total` 必须等于 `Σ qty×unit_price`（`audit` 校验）。

`resources.bookings[]`：`{resource, sessions, session_min, window_min, unit_price, basis_locator}`，所需机时 `sessions×session_min` 不得超 `window_min`。

`schedule`：`chain[]`（关键链步骤及 `duration_min`）、`parallel_groups[]`（可并行分组的步骤时长）、`total_elapsed_min`（≥关键链且≥最长并行组）、`calendar_days`。预算/机时/排期须可排期可报价。

### 3.5 参数范围

`parameters[]` 每项：

```json
{"name":"", "value":null, "value_or_range":null, "unit":"",
 "domain":[], "equipment":"", "basis":"", "basis_locator":"", "outside_range_action":""}
```

- `basis` 枚举：`instrument_manual | reagent_label | organizer_contract | literature | pilot_data | kit_insert | assumption`。
- 有限参数域必须落 `domain`；设备量程必须落 `equipment` 对应的 `resources.equipment[].range`（`audit` 校验）。
- 非 `assumption` 的数值必须给 `basis_locator`；`assumption` 必须给 `outside_range_action`。

### 3.6 风险与异常

`risks[]` 每项：

```json
{"id":"", "failure_mode":"", "detection_criterion":"", "stop_loss":"", "mitigation":"", "contingency":""}
```

覆盖污染、无信号、重复性差、样本不足等失败模式，写明判据、止损点、替代路径；不得只写「注意安全」。

## 4. 候选方案比较与修订

案例给出 3 个版本化候选方案：`candidates[]` 每项 `{id, label, summary, hard_constraint_check:[{constraint, pass, detail}], soft_score, rejected_reason}`；`selected_candidate` 指向其一；`revisions[]` 记录相对被选方案做了哪些修改及理由。

硬规则：被拒绝者写 `rejected_reason`；被选方案必须通过**全部**硬约束（`check` 校验）；比较依据写进 `evidence`。

## 5. 证据与溯源

`evidence[]` 每项：

```json
{"claim":"", "locator":"", "quote":"", "supports":""}
```

- `locator` 格式：`materials/paper.md`（本地文本）、`paper.pdf#p3`（PDF 页码）、`case.json:contract` 等。
- `quote` 必须是从材料**逐字**摘录的原句（非复述、非翻译改写）；`check` 会在本地文本中逐字核对（PDF 抽取可能改断词，故 PDF 来源只降级为警告）。
- 同一指标两处来源不一致：两处都记录，并在 `source_conflicts[]` 标注 `{metric, source_a, source_b, impact}`。
- 不虚构引用/DOI/数值/价格；缺失写 `not_reported` 或 `pending_human`。

## 6. 校验器规则（`check` 的判定）

| 检查 | 类型 | 失败码 |
|-|-|-|
| 信封键齐全、status 合法、类型正确 | error | `envelope_missing` / `status_invalid` / `envelope_type` |
| 契约每个叶键存在、根键存在 | error | `contract_key_missing` / `contract_root_key_missing` |
| 六要素非空 | error | `six_element_empty` |
| status=ok 但六要素未齐 | error | `status_not_honest` |
| 参数 `basis` 合法、单位存在、非 assumption 给 locator | error/warn | `parameter_basis_invalid` / `parameter_unit_missing` / `parameter_locator_missing` |
| 对照未写目的/排除混杂 | warn | `control_purpose_missing` |
| 候选<3、拒绝缺理由 | warn | `candidate_count_low` / `rejected_reason_missing` |
| 预算/预约/样本量/排期/量程/参数域算术 | error | `arithmetic_violation` |
| 证据缺 locator/quote、逐字不匹配 | error/warn | `evidence_missing_locator` / `evidence_missing_quote` / `evidence_quote_not_found` |
| plan.md 缺失或不一致 | error/warn | `plan_md_missing` / `plan_md_stale` |

退出码：0 = 可用（允许警告）；1 = 有 error 必须修复；`--strict` 时警告也返回 1。报告写 `<work>/check_report.json`。

## 7. 评分维度 → 自检 → 常见失败与修复

| 评分维度 | 自检 | 常见失败 | 修复 |
|-|-|-|-|
| 任务完成度 | 目标→可检验假设；六要素可操作 | 复述目标、变量无单位、步骤无时长 | 写 H0/H1+判定规则；每变量配单位/量程；每步骤绑定设备/试剂/时长 |
| 正确性 | 变量与测量匹配、对照排除混杂、参数在量程内、引用真实 | 参数超量程、只有空白对照、引用拼凑 | 用 `audit` 查量程/域；补阳性/阴性对照；逐字摘 quote |
| 完整性 | 六要素非空 | 缺风险/时间成本 | 按第 3 节补齐每项字段 |
| 可核验性 | 版本+来源+金标准+change_log | plan.md 与 json 不一致 | `render` 重生成；`check` 验证 |
| 使用增益 | 3 候选比较、算术裁剪 | 只写一个方案、无理由淘汰 | 填 `candidates/revisions/rejected_reason`，跑 `audit --write` |
| 创新增益 | 反推对照/裁剪参数/风险预案至少一类 | 模板式空话 | 用「设备量程」裁剪参数、用「研究目标」反推对照、写止损点与替代路径 |
| 可靠体验 | 一次生成可判断可用 | 空输入崩溃、坏 JSON 中断 | 降级到 `insufficient_evidence`；记录后继续 |
| 生态贡献 | 输入清单/输出结构/依赖/示例/限制齐全 | 缺示例或限制 | 补第 8 节示例与边界说明 |

## 8. 边界与限制

- 只依据 `/input` 真实材料；「前瞻性合同」仅标「案例给定约束（非真实报价/审批）」。
- 不替代实验人员的安全/伦理审查：相关项一律列 `pending_human`，不写成已批准事实。
- 无真实材料时输出「材料不足」而非示例填充。
- 同一输入重复运行：内容字段确定性一致（`ts` 除外）。
