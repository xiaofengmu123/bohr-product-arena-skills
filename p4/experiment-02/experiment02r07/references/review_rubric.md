# 实验方案可开工性审计 — 审查口径与判定规则

> 配套 `SKILL.md` 第 5 节。本文只规定"怎么判、凭什么判、写到哪"，
> 不写任何正式案例答案；判定依据必须来自 `/input` 下的逐字材料。

## 1. 输出契约（result.json 顶层）

| 字段 | 要求 |
|---|---|
| `case_id` | 从 case.json 的 `case_id`/`id`/`challenge_id`/`armiId` 识别，缺失退化为 `unknown` |
| `status` | `ok \| partial \| insufficient_evidence \| input_error` |
| `research_question` | 研究目标原文（非转写）或 `null` |
| `experiment_plan` | `objective/hypotheses/assumptions/variables/controls/steps/parameters/resources/interpretation_criteria` |
| `review` | 七维审查，每维为审查项数组（见第 2 节） |
| `plan_comparison` | `criteria/plans/ranking/recommended/tie_break/notes` |
| `evidence_and_risks` | `evidence/risks/exception_handling` |
| `confirmation` | `safety/ethics/equipment_availability/expert_confirmation` |
| `sources` `uncertainty` `pending_human` `limitations` `errors` `degradations` `change_log` | 缺口与过程证据，缺省为 `[]` |
| `output_contract` | 题目契约的回填信息：`source/spec/echoed/kept_builtin/unfilled` |

**契约回填硬规则**：case.json 若含 `output_contract`，其全部字段必须**原样**回填到
result.json 顶层（缺值给 `null`/`[]`/`{}`/`""`），不改名、不删键、不替换为内置字段。

## 2. 七个维度与判定口径

审查项结构（`review.<dim>[]`）：

```json
{
  "id": "EQ-01",
  "dimension": "equipment_materials",
  "check": "设备型号/精度是否满足方案步骤 N 的要求",
  "verdict": "conditional",
  "resource_state": "需采购",
  "conditions": ["采购前确认 ±0.5℃ 精度"],
  "blocks": [],
  "evidence": [{"locator": "case.json#plans[1].steps[2]", "quote": "逐字原文", "kind": "plan"}],
  "checked_locators": [],
  "arithmetic": {"formula": "…", "result": "…", "unit": "…"},
  "confidence": "medium",
  "needs_expert": false,
  "owner": "设备管理员",
  "due": "开工前 10 个工作日",
  "notes": []
}
```

各维"检查什么"：

1. **假设与变量**：研究目标是否转成可检验问题；自变量/因变量/混淆变量是否命名与可测；
   假设是否有来源或需标注为待验证。证据来自方案目标段、变量定义、文献依据。
2. **对照组**：对照类型（空白/阳性/阴性/自身前后）、对照样本来源、随机化或配对、盲法；
   "没写对照"判 `missing`，不得默认满足。
3. **设备与材料**：型号、精度、量程、附件、耗材规格、货期、库存/预约状态；
   与 `contracts`/`price_list`/`booking` 原文比对，给 `resource_state`。
4. **时间与成本**：按步骤拆设备占用、人工操作、等待、检测周转；采购/审批前置；
   单价×数量与预算。所有数值必须用 `scripts/audit.py --calc` 算出来并带算式。
5. **参数范围**：参数值是否落在 `parameter_domains` 的有限域内（枚举/区间/步长）；
   越界要给出到最近边界的距离；域缺失标 `unconfirmed`。
6. **风险与异常**：设备故障、批次差异、样本不足、参数越界、缺页/缺数据；
   每条给降级路径、替代方案与转人工节点；信息不足标 `unconfirmed`。
7. **伦理与安全**：是否涉及人体/动物/危险试剂/个人数据；对应审批级别、所需材料、
   大致周期；未经审批可查证时 `unconfirmed + needs_expert=true`，不写"已通过"。

## 3. 判定状态机（verdict）

| verdict | 何时用 | 硬性字段 |
|---|---|---|
| `ok` | 有原文依据且与约束一致 | `evidence`（locator + quote） |
| `conditional` | 可行但需满足条件 | `evidence` + 非空 `conditions`（逐条可执行） |
| `blocked` | 缺设备/材料/样本/审批等，当前无法开工 | `evidence` + `blocks` 或 `conditions` 写清缺什么 |
| `unconfirmed` | 方案写了但无确认来源 | `evidence`（若有）+ `notes` 写明需谁、确认什么、约多久 |
| `missing` | 方案没写，不得默认满足 | `checked_locators` 标注在哪几处查过而未见 |
| `conflict` | 同一指标两来源不一致 | 两条证据都记录 + `notes` 标 `source_conflict` |

**核心三条**：①"方案没写"≠"方案满足"，未写即 `missing`；② 时间/参数/样本必须算出来，
不靠感觉；③ `conditional` 写清条件、`blocked` 写清缺口、`unconfirmed` 写清确认路径。

`resource_state` 仅设备材料维：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。

## 4. 算术规则（对应 audit.py 运算）

| 场景 | 运算 | 输出 |
|---|---|---|
| 步骤时长累加 | `sum_time`（串行求和、并行取最大、重复×次数） | 分钟 + 人工可读 + 算式 |
| 依赖工期 | `timeline`（拓扑排序 + 关键路径 + 截止松弛） | makespan/关键路径/松弛 |
| 费用 | `cost`（单价×数量，分币种，费用项，预算超支） | 分币种总额/超支额 |
| 参数域 | `range`（枚举/区间/步长网格） | in/out_of_domain/off_grid + 到边界距离 |
| 方案排序 | `weights`（归一化）+ `score`（min-max 标准化加权） | 排序 + 并列裁决 |
| 样本 | `sample_check`（保留率折算可用量）+ `sample_size`（正态近似） | 差额 / 每组样本量 |
| 容差/阈值 | `tolerance` / `gate` / `lead_gate` | 偏差 / 通过与否 / 前置可行性与松弛 |

**诚实原则**：缺值或不可解析的量 → `unconfirmed` 并从总数剔除（列 `excluded`），
**绝不静默当 0**；近似换算（月=30 天、年、工作日=8h）必须在 `warnings` 标注。

## 5. 方案比较（plan_comparison）

多版候选方案时：先定 `criteria`（权重、方向 min/max，权重用 `weights` 归一化），
再对每个方案填 `scores`，由 `score` 运算排序。排序并列时给 `tie_break`
（默认：阻断项少者优先 → 成本低者优先 → plan_id 字典序），并在 `notes` 说明。

## 6. 确认项（confirmation）

四类各为数组，条目写明：`item`（确认什么）、`status`（`confirmed|pending|na`）、
`owner`（谁确认）、`due`（何时前）。未经确认的建议**不得**写成可执行事实；
写 `pending` 并放入 `pending_human`。

## 7. status 语义与收口

| status | 含义 | 触发 |
|---|---|---|
| `partial` | 骨架/部分审查完成，诚实标注缺口 | 缺任一维、未做算术或未收口 |
| `ok` | 七维均非空、算术有算式、证据可溯源、无致命 errors | S5 自检通过后由模型改写 |
| `insufficient_evidence` | 材料不足，只列缺口不编造 | 无可用方案/目标/材料 |
| `input_error` | 输入目录缺失或全部不可解析 | 无法读取任何 case |

`selftest.py` 会把"status=ok 但某维为空 / errors 非空"判为 FAIL，防止提前宣告完成。

## 8. 报告渲染

`review_report.md` 由 `audit.py --render-report` 从 result.json **确定性生成**，
保证报告与结构化结果同源；手工只改 result.json，再重新渲染，避免两份内容漂移。
