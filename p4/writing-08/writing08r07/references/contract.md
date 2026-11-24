# 输出契约字段与降级矩阵

本文件是 result.json 的字段规范与异常处置参考，供写作 Agent 回填和自检使用。

## 1. result.json 顶层字段规范

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 来自 case.json 的 case_id/id/challenge_id，缺省 `"unknown"` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error` |
| （output_contract 字段） | 原样 | 键名、数量与 case.json 一致；未知值 `null`/`[]`/`{}` |
| `artifacts` | list | 可编辑产物路径，**相对 `/app/output/`** |
| `sources` | list | 实际读取的材料清单（路径 + 类型） |
| `uncertainty` | list | 不确定项：冲突、无来源、估计值，逐条说明 |
| `change_log` | list | 步骤级变更记录，追加不改写，可回退 |
| `errors` | list | 输入/运行错误（坏 JSON、坏编码、写盘失败等） |
| `degradations` | list | 降级处置（超大文件跳过、PDF 未抽取等） |
| `limitations` | list | 具体缺口/失效场景，不用套话 |
| `pending_human` | list | 可执行的人工确认项（补哪个数、问哪句） |
| `runtime` | object | 用时、已用请求数、预算（900s/64 请求） |

## 2. status 语义与诚实原则

| status | 何时用 | 禁止 |
|---|---|---|
| `ok` | 六要素齐全、证据齐备、validate 通过 | 材料不足时不得写 ok |
| `partial` | 成稿可用但有未确认项/缺口 | 不得静默隐藏缺口 |
| `insufficient_evidence` | 材料缺失或不足以支撑章节 | 不得生成示例答案 |
| `input_error` | case.json 坏 JSON 等输入级错误 | 不得吞掉错误继续假装正常 |

## 3. 鲁棒性降级矩阵（与 scripts 行为一致）

| 场景 | 退出码 | result.json | 附加记录 |
|---|---|---|---|
| 空输入目录 | 0 | `status=insufficient_evidence` | limitations + pending_human |
| case.json 坏 JSON | 0 | `status=input_error` | errors.bad_json |
| 缺 --input/--output/--work | 0 | 用缺省 `/input` `/app/output` `/app/work` | — |
| 单文件 >2MB | 0 | 继续处理其它文件 | degradations.oversized_skipped |
| 坏编码文本 | 0 | 逐编码兜底读取 | degradations.encoding_fallback |
| 写盘失败 | 非 0（已尽力降级） | 最小合法产物 | errors.write_failed |
| 缺 PDF/Office 工具 | 0 | 材料标 needs_tool | degradations.not_extracted |

## 4. locator 与引用路径约定

- 文本材料：`materials/<file>#L<line>`；PDF：`<file>#p<page>`。
- `result.json` 内产物路径一律相对 `/app/output/`（如 `manuscript.md`、`evidence_map.json`），不得写绝对路径。
- 中间产物（`materials_inventory.json`、`extracted_facts.json`、`state.json`、`validation_report.json`）写 `/app/work/`，不进入最终 artifacts。

## 5. 确定性要求

同一输入两次运行，除 `change_log`/`runtime` 中的时间戳外，`result.json` 与中间产物必须逐字节一致。实现方式：有序字典、`sort_keys`、固定遍历顺序、不用随机数、不用哈希无序迭代。
