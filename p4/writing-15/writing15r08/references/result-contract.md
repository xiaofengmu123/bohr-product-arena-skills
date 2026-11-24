# result.json 输出契约与状态约定

`scripts/main.py assemble` 负责生成最终 `/app/output/result.json`。本文件说明字段语义与取值，
供 Agent 在写作过程中按此填写 `work/evidence.json` 与 `work/delivery_meta.json`，并供他人复核。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string\|null | 从 `case.json` 的 `case_id`/`id`/`caseId` 回填 |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error` |
| `artifacts` | array | 目标产物，元素 `{path,type,size}`；可编辑格式（.md/.docx）应配渲染预览（.pdf/.png） |
| `evidence_map` | array | 事实与来源映射，见下 |
| `sources` | array | 源文件清单，来自 `work/inventory.json` 的 `files` |
| `change_log` | array | 版本与修改记录，元素 `{step,time,action,target,note}` |
| `pending_human` | array[string] | 需要人工确认的事项 |
| `uncertainty` | array[string] | 不确定/存疑项 |
| `limitations` | array[string] | 材料或软件缺口 |
| `errors` | array[string] | 读取/解析异常 |
| `degradations` | array[string] | 降级处理记录 |
| `runtime` | object | `{started_at,finished_at,phase}` |
| `check_report` | object | 可选，一致性检查摘要（`issues`+`counts`） |

外加 `case.json` 中 `output_contract` 的全部键：逐键原样回填，缺值给 `null`（列表型给 `[]`），
不改名、不删除未知键。

## status 取值（诚实原则）

- `ok`：产物齐备、无 error 级一致性问题（或已标注人工确认）。
- `partial`：已有可用产物，但存在待修问题或缺口；**中途异常时的默认落盘状态**。
- `insufficient_evidence`：无材料或材料为空，不足以完成写作；只列缺口，不编造。
- `input_error`：`case.json` 缺失/不可解析且无法继续。

`assemble` 默认按「材料有无 → 产物有无 → error 级问题数」推导；若写入了
`work/delivery_meta.json` 的 `status`，则以显式声明为准并记录 `status_override`。

## evidence_map 元素（`work/evidence.json` 的 `entries`）

```json
{"id":"e01","claim":"……","kind":"number|term|citation|claim|figure|table",
 "source":"materials/ch3.md","locator":"L12-14 或 #段落定位","quote":"材料逐字片段",
 "status":"matched|conflict|unverified|pending"}
```

要求：`quote` 必须是材料中的逐字片段；同一指标多来源不一致时记两条并标 `conflict`，
不要隐藏冲突；找不到来源标 `unverified`，需要人工确认标 `pending`。

## 版本与边界（change_log）

- 每次 `scan`/`check`/`assemble` 及每轮重要修改都要追加一条。
- 修改记录应写清「改了什么、为什么改、改前原文、改后文本」，使一次修改可回退、可比较。
- 冲突、缺口、超长截断、格式不支持等，同时写入 `uncertainty`/`pending_human`/`limitations`。

## 降级与异常

- 坏 JSON / 坏编码：捕获后记入 `errors`，继续处理其余材料，进程退出码 0。
- 缺参数：默认 `/input`、`/app/output`、`/app/work`。
- 写盘失败：降级为最小合法产物，不崩溃。
- 缺 PDF/Office 渲染：主文本仍交付 `.md`，预览缺失记入 `degradations`，不伪造渲染结果。
