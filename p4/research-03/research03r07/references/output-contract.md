# 输出契约与字段说明

本文档定义 `/app/output/result.json` 的字段、状态语义、降级阶梯与鲁棒性规则，供 Agent 与复核者对照。

## 1. result.json 结构

result.json 必须存在且可被 `json.load` 解析，包含两部分：

### 1.1 case_id

- 取 `case.json` 的 `case_id/caseId/id/armiId/challenge_id`（任一命中即用），缺失填 `"unknown"`。

### 1.2 题目 output_contract 的全部字段（原样回填）

- 从 `case.json` 的 `output_contract`（可能在顶层，或嵌套在 `task/output/spec/requirements` 下）自动发现字段名。
- 缺值填 `null`/`[]`（按声明类型推断 array/object/scalar），**不改名、不删未知键**；`scripts/main.py skeleton` 自动做这件事。
- 若 case.json 无 `output_contract`，使用默认领域契约：`review_question`、`subquestions`、`search_strategy`、`inclusion_criteria`、`exclusion_criteria`、`screening_records`、`prisma_flow`、`quality_appraisal`、`synthesis`、`evidence_map`、`uncertainty`。

### 1.3 诚实字段（追加，不被题目契约取代）

| 字段 | 类型 | 含义 |
|---|---|---|
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `sources` | list | 输入来源清单（path/kind/bytes），由 inventory 派生 |
| `uncertainty` | object | `conflicts/bias/gaps/undetermined` 结构化不确定性 |
| `change_log` | list | `{ts, action, detail}` 增量修改留痕 |
| `limitations` | list | 材料/方法/权限等限制，如实标注 |
| `errors` | list | 解析、读写等可恢复错误 |
| `degradations` | list | 降级处理记录（截断、编码回退、not_run 等） |
| `pending_human` | list | 需人工确认的缺口 |
| `runtime_seconds` | number | 运行耗时（由 `run_state.started_at` 计算） |

诚实字段若与题目契约同名（如 `status`），以诚实字段为准，保证状态与证据链不被题目键覆盖。

## 2. status 语义

- `ok`：五环节完成且证据可回链（review_question、screening_records、evidence_map、synthesis.conclusions 均非空）。
- `partial`：部分完成，`limitations` 必须列出缺口。
- `insufficient_evidence`：材料不足，不编造结论。
- `input_error`：case.json 等输入不可解析。

`finalize` 依据上述四要素自动判定，Agent 不手改 status；证据不足时不得强行 `ok`。

## 3. 配套可复核产物（写 `/app/output/`）

- `review.md`：人类可读摘要（研究问题、PRISMA 计数、结论、冲突、空白、局限）。
- `screening_log.json`：两轮筛选记录（即 `screening_records`）。
- `evidence_map.json`：证据矩阵（id/record_id/claim/locator/quote/strength）。

## 4. 降级阶梯（鲁棒性核心，禁止编造）

| 场景 | 处理 |
|---|---|
| 空输入目录 | `status=insufficient_evidence`，`limitations` 说明，退出码 0 |
| 坏 JSON | 记 `errors`，`status=input_error`，继续处理其余材料 |
| 坏编码 | `utf-8-sig → gb18030 → latin-1` 顺序降级；latin-1 回退记 `degradations` |
| 缺参数/未知参数 | `--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`；未知参数忽略并告警 |
| 单文件 >2MB | 截断读取并记 `degradations`（`oversized`） |
| 检索为空/数据库不可用 | `run_status=not_run`、`hits=null`，记 `degradations`，不伪造命中数 |
| 全文不可得 | 该篇标 `abstract_only`，结论降为 `suggestive`/`not_reported` |
| 条款冲突/证据矛盾 | 并列记录 + 标 `undetermined`，不强行取一方 |
| 写盘失败 | 降级为最小合法产物，进程不崩溃，退出码 0 |
| 缺字段/未知字段 | 保留键填 `null`/`[]`，记 `errors`/`degradations`，绝不编造文献/DOI/数值 |

所有命令（discover/skeleton/merge/finalize/selfcheck）都必须退出码 0，问题写入返回 JSON 的 `errors`/`degradations`；顶层 `main()` 有兜底 except，异常时也写一份最小合法 result.json。

## 5. 自检维度映射

`scripts/main.py selfcheck` 校验以下点（对应评分维度）：

- result.json 可解析（运行稳定性）
- case_id 非 unknown（输入标识）
- status 取值合法（诚实状态）
- 题目契约字段无缺失（结果完整性）
- 每条结论 `evidence_ids` 命中 `evidence_map`（证据回链）
- 每条 exclude 有 `reason + criterion_id`（筛选可核验）
- PRISMA 计数与重算一致（可复跑）
- 每条证据有逐字 `quote`（证据溯源）

`scripts/selftest.py` 额外跑五类鲁棒性场景：空输入、坏 JSON、坏编码、缺参数、超大文件，全部通过才退出 0。
