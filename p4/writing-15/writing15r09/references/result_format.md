# result.json 与产物格式规范

## result.json 顶层结构

```json
{
  "case_id": "case-xxx",
  "status": "partial",
  "<output_contract 字段...>": null,
  "sources": [],
  "uncertainty": [],
  "change_log": [],
  "evidence_map": [],
  "artifacts": [],
  "pending_human": [],
  "errors": [],
  "degradations": [],
  "limitations": [],
  "runtime": {"seconds": null, "requests": null}
}
```

- `case_id`：来自 `/input/case.json`，缺失则 `null` 并在 errors 记录。
- `status`：`ok`（完成且契约字段齐全）/ `partial`（部分完成）/ `insufficient_evidence`（材料不足）/ `input_error`（输入不可解析）。
- 契约字段：题目 `output_contract` 的每个键都必须出现在顶层，缺值给 `null`/`[]`/`{}`，不得改名、不得删除未知键。契约原始副本保存于 `/app/work/output_contract.json`。

## 各字段约定

| 字段 | 类型 | 约定 |
|---|---|---|
| `sources` | list | `{id, path, kind, role}`；kind ∈ paper/note/table/figure/code/other；role 说明用途 |
| `uncertainty` | list | `{item, reason, impact}`；含 `source_conflict`、`not_reported`、`pending_human` 情形 |
| `change_log` | list | `{target, before, after, reason, locator}`；before/after 保留原句与改后句 |
| `evidence_map` | list | `{claim, locator, quote, source_id}`；quote 必须逐字来自源文件 |
| `artifacts` | list | 产出的可编辑文件路径（相对 `/app/output/`）与格式 |
| `pending_human` | list | 需要作者/导师确认的项 |
| `errors` / `degradations` | list | 运行错误 / 降级（坏编码、超限跳过、缺软件） |
| `runtime` | dict | 秒数与模型请求数，从平台记录取，**不编造** |

## 可编辑产物规范

- 章节/稿件：`.md`（UTF-8）；一个文件一章，文件名含章节编号，如 `chapter-03-methods.md`。
- 结构化数据：`.json` 或 `.csv`；术语表、符号表、引用表、改动记录优先 `.md` 或 `.json`。
- 图形：可编辑 `SVG` + 真实 `PDF`；Office/幻灯片：`.docx`/`.pptx`/`.xlsx`，并用 office-renderer 渲染真实 PDF/PNG 预览。
- 预览文件与可编辑文件并列，供评审快速检查；文件名以 `-preview` 或与源同名不同扩展名区分。

## locator 与 quote 规范

- `locator` 形如 `materials/chapter-03.md#L42`、`paper.pdf#p3`（提取文本后按页定位）、`data/table1.csv:5`。
- `quote` 为源文件中的**逐字**片段（忽略空白差异）；不能是转述或臆造。
- 同一结论引用多个来源时，写多条 evidence_map 条目，不要合并成一个含糊的来源。
- 冲突信息两条都记录，并在 `uncertainty` 标 `source_conflict`。
