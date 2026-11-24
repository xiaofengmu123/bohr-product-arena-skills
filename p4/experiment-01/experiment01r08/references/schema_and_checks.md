# 结果结构与检查规范（schema_and_checks）

本文件是 `SKILL.md` 的配套细则，给字段语义、fallback 结构、六要素定义、算术约定与证据规范。`scripts/main.py` 的 `scaffold`/`validate` 按本文件执行。

## 1. 输入清单（运行时 /input）

| 类别 | 常见形式 | 处理 |
|-|-|-|
| 案例描述 | `case.json`（含 `case_id`、可选 `output_contract`、内嵌候选方案/合同/参数域） | 容错 JSON 解析；坏 JSON 记 `errors` 并回退 |
| 任务说明 | `task.md`（含 `challenge_id`、评分维度、约束） | 纯文本读取，抽 `challenge_id` |
| 材料 | `materials/*`、`*.md/*.txt/*.csv` | 逐行扫描候选/合同/参数提示 |
| 论文 | `*.pdf` | `pdftotext -layout` 提取到 `/app/work/pdf_text/`；失败记 `degradations` |
| 资源合同 | `resources/*`、`contract*`、`price*`、`booking*` | 只作“案例给定约束”，非真实报价/审批 |
| 候选方案 | `candidate_plan_*.md`、`plans[]`、`variants[]` | 每案例通常 3 个版本，需逐项比较 |

不假设固定文件名/键名；`probe` 会输出 `files`、`candidate_plans`、`contracts`、`parameter_domains`、`pdfs`、`hints`（带 `rel#L行号` 定位）。

## 2. 信封字段语义

| 字段 | 类型 | 含义 |
|-|-|-|
| `case_id` | str | 案例标识；取 case.json → task.md `challenge_id` → `"unknown"` |
| `status` | enum | `ok`（完整可执行）/`partial`（有骨架或部分缺口）/`insufficient_evidence`（材料不足）/`input_error`（输入不可用） |
| `sources` | list | 用到的来源，每项含 `locator`（+可选 `kind`/`title`） |
| `uncertainty` | list | 不确定项、冲突双方与影响、两种替代 |
| `pending_human` | list | 必须人工确认的事项（安全/伦理/设备/专家） |
| `change_log` | list | `{ts, event, detail}`，增量记录 |
| `errors` | list | 解析/输入错误（可空） |
| `degradations` | list | 降级说明（缺软件/超大文件/坏编码） |
| `runtime` | dict | `started_at`、`probe_runtime_ms` 等运行元信息 |

`status` 诚实性：`status=ok` 时六要素必须全部非空，否则 `validate` 报错。

## 3. fallback 输出结构（无 output_contract 时）

```json
{
  "plan": {
    "research_question": null,
    "hypotheses": [],
    "variables": [],
    "controls": [],
    "steps": [],
    "parameters": [],
    "resources": [],
    "readout_criteria": [],
    "time_cost": {"total_hours_estimate": null, "schedule": [], "budget": [], "note": null}
  },
  "evidence_and_risks": {"evidence": [], "risks": []},
  "confirmations": {"safety": [], "ethics": [], "equipment_availability": [], "expert_review": []},
  "candidate_comparison": [],
  "arithmetic_validation": [],
  "selected_plan": null,
  "case_id": "...",
  "status": "partial",
  "sources": [], "uncertainty": [], "pending_human": [], "change_log": [],
  "errors": [], "degradations": [], "runtime": {}
}
```

有 `output_contract` 时，`scaffold` 逐键原样回填（dict 递归、list→`[]`、标量→`null`），**不改名、不删未知键**；信封字段叠加在最外层。

## 4. 六要素定义与检查

| 要素 | 主字段 | 可操作化要求 |
|-|-|-|
| 假设与变量 | `plan.hypotheses`、`plan.variables` | 假设可证伪；变量有操作定义、测量方式、单位、量程；区分自变量/因变量/混杂 |
| 对照组 | `plan.controls` | 空白/阳性/阴性/剂量梯度；每条写明排除哪类混杂 |
| 设备材料 | `plan.resources` | 型号/量程/耗材规格/试剂；标可替代与不可替代 |
| 时间成本 | `plan.steps`（时长）、`plan.time_cost.budget` | 步骤时长、并行关系、样本量、预算；可排期 |
| 参数范围 | `plan.parameters` | 参数、单位、允许区间、超界处置、依据来源 |
| 风险与异常 | `evidence_and_risks.risks` | 失败模式、判据、止损点、替代路径 |

`validate` 以键名递归匹配（大小写不敏感，含中英文别名）判断六要素是否非空；任一为空记 `WARN`，若同时 `status=ok` 则升级为 `ERROR`。

## 5. 算术约定（validate 强制）

**预算**（键名命中 `budget/costs/cost_items/预算/费用` 的列表）：

- 每条 `{unit_price, quantity, subtotal}`：`subtotal` 必须等于 `unit_price × quantity`（容差 `1e-6`）。
- 同级父对象的 `total/total_cost/amount/总计/合计` 必须等于该列表 Σsubtotal。
- 不一致 → `ERROR`。

**排期**（键名命中 `schedule/steps/procedure/步骤/排期` 的列表）：

- 每步 `{id|step_id|name, duration_min|duration_hours, depends_on[]}`。
- 按 DAG 求关键路径（最长路径）；父对象 `total_duration_min/total_min/总时长` 必须等于关键路径。
- 依赖成环 → `ERROR`；不一致 → `ERROR`。

无法计算的条目（缺 duration/quantity）不报错，记入 `arithmetic_validation` 的“不可核验”说明。

## 6. 证据规范

```json
{"locator": "materials/paper.pdf#p5", "quote": "原文逐字片段", "kind": "literature"}
{"locator": "resources/price_list.md#L12", "quote": "单价 1200 元/次", "kind": "contract", "note": "案例给定约束，非真实报价"}
```

- 每条关键结论/数值带 `locator` + 逐字 `quote`；`validate` 对缺失者记 `WARN`。
- 同一指标来源冲突：两条都记，`note` 标 `source_conflict`，并在 `uncertainty` 写影响。
- 材料未给：写 `"not_reported"`，不补造数值/文献/价格/排期。

## 7. 状态与降级对照

| 情形 | status | 必写信封 |
|-|-|-|
| 材料充分、六要素完整、算术通过 | `ok` | sources/uncertainty/change_log |
| 有骨架、部分要素待补 | `partial` | pending_human 列缺口 |
| 输入为空或关键材料缺失 | `insufficient_evidence` | uncertainty 说明缺什么 |
| 输入不可解析且无法回退 | `input_error` | errors 记原因 |

降级永不编造：缺 pdftotext → `degradations` 记“无法提取 PDF 正文”；超 2MB → 跳过并记；坏 JSON → `errors` 记并继续。
