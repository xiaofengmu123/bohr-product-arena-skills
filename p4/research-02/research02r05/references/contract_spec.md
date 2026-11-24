# result.json 输出契约规范

本文件定义 `/app/output/result.json` 的字段级契约，是 `scripts/main.py`
与 `scripts/validate_result.py` 的共同依据。目标：**result.json 与题目
`output_contract` 逐字段对齐**，任何缺值、未知键、嵌套结构都不被丢失或改名。

## 1. 顶层必含字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 取自 `case.json` 的 `case_id/caseId/id/challenge_id/armiId`；缺失用 `case.json` 文件名 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `<contract 字段…>` | 按契约 | 见 §2，键名与嵌套结构原样保留 |
| `sources` | list | 真实来源清单，每项含元数据与定位 |
| `uncertainty` | object | `conflicts` / `biases` / `gaps` / `undetermined` 四类 |
| `pending_human` | list | 需要人工确认的缺口与决策 |
| `change_log` | list | 增量变更记录，每项含 `ts/step/action/detail` |
| `errors` | list | 读取/解析类错误 |
| `degradations` | list | 缺全文、缺软件、超限跳过等降级 |
| `runtime` | object | `started_at/phase0_done_at/elapsed_s` |
| `artifacts` | object | 五个主产物的路径与状态（pending/done） |

## 2. 契约字段对齐规则

- `output_contract` 的多种形态都要能归一化：`{"properties": {...}}`、
  `{"fields": ...}`、`name -> spec` 映射、`[{"name":...}]` 列表、单字符串。
- 类型缺省：`array -> []`、`object -> {}`（含嵌套 `properties` 逐层展开）、
  其余（string/number/boolean/无类型）`-> null`。
- 未知键不删、不改名；`required` 中出现但 `properties` 未列出的字段也要占位。
- 诚实字段（`status/sources/uncertainty/pending_human/change_log` 等）与契约键
  重名时，诚实字段优先，并在 `change_log` 记录 `key_collision`。
- 校验（`validate_result.py`）重新解析 `case.json` 的契约，独立核对：每个
  契约字段都出现在 result.json 顶层、且类型一致。

## 3. status 语义（诚实原则）

| status | 含义 | 必须附带 |
|-|-|-|
| `ok` | 四步完成、证据可回链 | 五个主产物齐全、evidence_table 行含 locator+quote、review 含 `[S#]` |
| `partial` | 部分完成 | `uncertainty.gaps` 或 `pending_human` 列出缺口 |
| `insufficient_evidence` | 材料不足 | 同上，且 `sources` 可为空，不编造结论 |
| `input_error` | 输入不可解析 | `errors` 记录具体原因 |

## 4. 降级映射（不允许编造）

| 场景 | 处置 |
|-|-|
| 仅有摘要/无全文 | 该篇标 `abstract_only`，结论降为 `suggestive`/`not_reported` |
| 元数据不全 | 该字段写 `not_reported` |
| 同一指标两源不一致 | 都记录并标 `source_conflict`，不静默择一 |
| 缺 `pdftotext` | 记入 `degradations`，用已抽取文本继续，不伪造解析 |
| 材料不足以回答问题 | `status=insufficient_evidence`，列出缺口 |
| 单文件 > 2MB | 跳过并记 `skipped: size_exceeds_2MB` |
| 内容哈希相同的文件 | 后者标 `duplicate_of`，只留一份入证据 |

## 5. 变更记录（change_log）要求

- 每个 Phase 结束追加一条，禁止最后一次性倾倒。
- 至少包含：Phase 0 的 `skeleton_written`；此后每次状态翻转
  （partial→ok）都要有 `action` 说明改动。
- 键重名、契约缺省、降级原因等异常都要落 change_log 或 errors/degradations。
