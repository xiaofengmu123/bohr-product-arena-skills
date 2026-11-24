# 输出契约对齐规范（contract-spec）

本包的核心差异点：`result.json` 的字段逐一对齐 `case.json` 的 `output_contract`，并用 `contract` 台账证明对齐过程可复核。

## 1. 对齐规则

1. 读取 `case.json` 的 `output_contract`（若为 dict）。
2. 递归展开其全部键路径，记录到 `result.json.contract.keys`。
3. 每个键路径**原样回填**进 `result.json` 顶层（嵌套 dict 递归合并）：
   - 非信封键直接 deepcopy；
   - 与信封键同名的 dict 递归合并，标量/数组冲突写入 `contract.collisions`；
   - 缺值给 `null` / `[]` / `{}`，不改名、不删未知键。
4. 回填后仍为 `null/""/[]/{}` 的键列入 `contract.unfilled`，供 Agent 增量补齐。
5. `selfcheck` 逐键核对：任何 `contract.keys` 里的路径取不到值即报 FAIL。

信封键（不可被覆盖，dict 递归合并）：`case_id, status, task, artifacts, review, sources, uncertainty, pending_review, pending_human, limitations, errors, degradations, change_log, runtime, contract`。

## 2. 顶层结构

```json
{
  "case_id": "来自 case.json，缺省用文件名",
  "status": "ok | partial | insufficient_evidence | input_error",
  "task": {"challenge_id": "...", "task_title": "..."},
  "artifacts": [{"path":"result.json","kind":"structured_result","role":"machine_readable"}, {"path":"report.md","kind":"review_report_markdown","role":"human_readable"}],
  "review": {
    "summary": "", "findings": [], "claims": [], "commitments": [],
    "coverage": {"factual_verification": {"status":"pending","checked":0,"note":""}, "...其余五维同构"},
    "section_map": [{"section":"...","paras":["P0001"],"pages":[1]}]
  },
  "sources": [{"path":"...","kind":"case|task|material","n_pages":0,"chars":0,"error":null}],
  "uncertainty": [], "pending_review": [], "pending_human": [],
  "limitations": [], "errors": [], "degradations": [],
  "change_log": [{"ts":"ISO8601 UTC","seq":1,"event":"skeleton_written|incremental_update","note":"...","status_after":"..."}],
  "runtime": {"seconds_elapsed":0.0,"model_requests_used":0,"budget":{"requests":64,"seconds":900}},
  "contract": {"source":"case.json.output_contract|default_schema","n_keys":0,"keys":[],"unfilled":[],"collisions":[]}
}
```

无 `output_contract` 时用 `default_schema`（即 `review` 块），使对齐机制恒生效。

## 3. change_log 台账

- 只增不改：`seq` 严格递增，`ts` 为 UTC ISO8601，`event` 记录骨架/增量/收尾事件。
- `update` 每次调用追加一条 `incremental_update`，并同步重算 `contract.unfilled`。
- `selfcheck` 校验 `seq` 严格递增与 `ts` 可解析。

## 4. status 语义（诚实）

| status | 何时使用 |
|---|---|
| `ok` | 六维均检查、findings 完整、无未决主结论 |
| `partial` | 已交付但存在未检查维度/待复核项/截断 |
| `insufficient_evidence` | 材料缺失或全部抽取失败，无法审查 |
| `input_error` | 输入目录缺失等无法开始的情况 |

## 5. 降级与异常

- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- PDF 抽取失败：`degradations` 记录 `pdftotext_not_found` 等，Agent 改用平台 pdf-text-tool 后把文本补进 `work/corpus.md`。
- 超长/截断：写入 `limitations`，不得宣称全文已查。
- 任何未确认项归入 `uncertainty` / `pending_review` / `pending_human`；绝不伪造 DOI、数值、引用或软件状态。
