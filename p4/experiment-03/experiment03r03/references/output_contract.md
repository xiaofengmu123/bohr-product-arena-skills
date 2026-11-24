# 输出契约（output contract）

本包交付两类结构化文件，二者字段一致、路径互相对应：

- `/app/work/plan.json`：Agent 编辑的工作稿（内容 + 元数据）。
- `/app/output/plan.json`：finalize 归一化后的正式结构化规格。
- `/app/output/result.json`：最终结果（含 case_id / status / 运行元数据）。
- `/app/output/plan.md`：可打开可编辑的可读方案。

## 1. result.json 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 取自 `/input/case.json`；无法取得时写空串并降级 status |
| `status` | enum | 是 | `ok \| partial \| insufficient_evidence \| input_error` |
| `plan` | object | 是 | 主结果正文，字段见第 2 节 |
| `title` | string | 是 | 方案标题 |
| `sources` | array | 是 | 证据映射，每项 `{locator, quote, claim}`，`quote` 为逐字引用 |
| `uncertainty` | object | 是 | `{unverified_values[], missing_data[], assumptions[]}` |
| `limitations` | array | 是 | 如实说明材料/软件/方法缺口 |
| `pending_human` | array | 是 | 需专家确认的安全/伦理/设备/工艺项 |
| `change_log` | array | 是 | 每项 `{step, action, reason}`；无墙钟时间，保证可复现 |
| `errors` | array | 是 | 可恢复错误（坏 JSON、坏编码等） |
| `degradations` | array | 是 | 降级处理记录（缺失文件、超限跳过等） |
| `artifacts` | array | 是 | 产物文件 `{path, type, description}`，路径均相对 `/app/output` |
| `input_fingerprint` | string | 是 | 输入的 sha256 指纹，用于核对两次运行输入是否一致 |
| `revision` | int | 是 | 变更日志条目数，作为确定性版本号 |

## 2. plan 正文字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `research_question` | string | 可检验的研究问题 |
| `objective` | string | 优化目标（含方向：最大化/最小化/达标） |
| `assumptions` | array | 明确假设 |
| `variables` | array | 自变量/被控变量 |
| `parameters` | array | 关键参数，见第 3 节 |
| `controls` | array | 对照与基线 |
| `procedures` | array | 有序步骤（保持作者顺序，不排序） |
| `resources` | array | 设备/材料/人员 |
| `response_metrics` | array | 响应指标，见第 4 节 |
| `interpretation_criteria` | array | 判读标准 |
| `search_strategy` | object | `{method, initial_points[], per_round, stop_criteria[], candidate_count, seed}` |
| `cost_budget` | object | `{items[], total, time_hours, tradeoff_note}`；`items` 每项 `{name, unit_cost, quantity, total, basis}` |
| `basis` | array | 方案依据 |
| `risks` | array | 风险 |
| `exception_handling` | array | 异常处置 |
| `confirmations` | array | 确认项 |

## 3. parameter 条目

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 参数名（唯一，用于排序与候选点生成） |
| `unit` | string | 单位 |
| `suggested` | number | 建议值，必须落在 `[min, max]` |
| `min` / `max` | number | 可行区间端点（设备量程/材料耐温/安全阈值） |
| `step` 或 `n_levels` | number | 网格步进或层数（doe 使用） |
| `constraint_source` | string | 约束来源（设备手册/材料规格/安全阈值/历史记录） |
| `basis_locator` | string | 证据定位（如 `materials/xxx.md#L3`） |
| `basis_quote` | string | 逐字引用，支撑上下限 |

## 4. response_metric 条目

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 指标名 |
| `definition` | string | 口径定义 |
| `unit` | string | 单位 |
| `method` | string | 测量方法 |
| `direction` | string | `maximize \| minimize \| target` |
| `target` | string/number | 目标值 |
| `accept_lower` | string/number | 可接受下限（或上限） |
| `prediction_method` | string | 预测方式：历史拟合 / 代理模型 / 经验规则 |
| `uncertainty` | string/number | 预测不确定度，必须披露 |

## 5. status 判定（确定性）

`finalize` 按纯函数规则从 plan 内容推导 status：

1. `research_question`、`objective`、`parameters`、`response_metrics` 全空 → `insufficient_evidence`。
2. 若 Agent 显式写 `status_hint`（工作稿专用字段，不进入正式产物）且合法，则采用。
3. 存在 `pending_human` / `limitations` / `unverified_values` / `missing_data` → `partial`。
4. 参数与响应指标均非空 → `ok`；否则 `partial`。
5. 输入全部不可读 → Agent 应写 `status_hint=input_error`。

## 6. 确定性保证

- 产物不含墙钟时间；`change_log` 使用固定 `step` 标识。
- 所有 JSON `sort_keys=True` + LF 换行；`plan.md` 按语义字段顺序渲染。
- `doe` 固定 `seed`、参数按名称排序、笛卡尔积子采样用 `random.Random(seed).sample`。
- 同一 `plan.json` 与同一输入，`finalize`/`doe` 两次运行产物逐字节一致；`selftest.py` 自动化验证。
