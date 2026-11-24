# result.json 契约与字段说明

本文件是可复核产物的字段规范，供 Agent、复核者与脚本共同遵循。脚本
`citation_lib.py` 是实现；此处只写规则，不包含代码。

## 1. 契约对齐（本 Skill 的核心不变量）

- 运行时读取 `/input/case.json`，若存在 `output_contract`（或
  `outputContract` / `contract`，`output.contract` 等兼容位置），则：
  1. 把其中**全部字段名**原样回填为 result.json 的顶层键；
  2. 键名不改名、不删除、不新增猜测键；
  3. 缺值用类型化占位：`array/list -> []`、`object/dict -> {}`、其余 -> `null`；
  4. 原始契约逐字保存到 `contract.mirror`，字段名保存到 `contract.fields`。
- 若 case.json **没有** `output_contract`，则 `contract.source = "absent"`、
  `contract.fields = []`，此时不存在“需要回填的题面契约字段”，标准结果字段
  （`results.* / sources / uncertainty / change_log` 等）仍照常写出，并在
  `limitations` 注明“case.json 未提供 output_contract”。
- 每次修改后重算 `contract.satisfied`（缺失键为空）与
  `contract.missing` / `contract.unfilled`（已存在但值为 `null` 的键）。

## 2. result.json 顶层结构

```jsonc
{
  "case_id": "string（必须）",
  "schema_version": "2.0.0",
  "status": "ok | partial | insufficient_evidence | input_error",
  "contract": {
    "source": "case.json#output_contract | absent",
    "fields": ["...", "..."],
    "mirror": { /* 原契约逐字快照，或 null */ },
    "satisfied": false,
    "missing": ["..."],
    "unfilled": ["..."]
  },
  // 此处为 output_contract 的全部字段，原样回填
  "results": {
    "papers": [/* 每条含稳定 id、元数据、evidence */],
    "edges": [/* source cites target，含 evidence */],
    "clusters": [],
    "paths": [/* 逐跳路径 */],
    "pruned": [],
    "graph_summary": { "nodes":0, "edges":0, "layers":0,
                       "hub_nodes":[], "bridge_nodes":[], "recent_hotspots":[] }
  },
  "sources": [],            // 每条带 locator + quote
  "uncertainty": { "conflicts":[], "missing":[], "coverage_gaps":[], "notes":[] },
  "change_log": [],         // 每次修改：seq/time/action/detail/reason
  "seed": null,             // 种子论文对象
  "seed_key": null,         // 种子唯一标识（DOI/arXiv/标题键）
  "research_question": null,
  "limitations": [], "pending_human": [], "errors": [], "degradations": [],
  "runtime": { "started_utc":"", "updated_utc":"", "seconds":0.0,
               "input_dir":"", "input_files":[],
               "request_budget": {"max_requests":64, "used_hint":null},
               "steps":[] }
}
```

## 3. 字段语义

| 字段 | 要求 |
|---|---|
| `case_id` | 从 `case.json` 的 `case_id / id / armiId` 读取，缺省 `unknown_case`，不伪造 |
| `status` | 诚实状态；只要骨架合法即可 `partial`，材料不足 `insufficient_evidence`，输入解析失败 `input_error`，全部完成 `ok` |
| `results.papers[]` | 每条含稳定 `id`、`title`、`year`、`authors`、`doi`/`arxiv_id`、`venue`、`evidence[]`；合并版本存 `merged[]` |
| `results.edges[]` | 统一方向 `source` cites `target`；带 `relation`、`source_label`、`target_label`、`evidence[]`；方向不确定标 `direction_unknown`，不猜测 |
| `results.paths[]` | `start`、`end`、`length`、`hops[]`（逐跳 `from`/`to`/`relation`/`direction`） |
| `sources[]` | 每条 `{locator, quote}`，`quote` 为材料中的逐字片段 |
| `uncertainty` | 冲突（标 `source_conflict`）、缺失、覆盖缺口分别记录 |
| `change_log[]` | 每条 `{seq, time, action, detail, reason}`；每步脚本自动追加，不依赖 Agent 手写 |
| `runtime` | 预算、输入清单、时刻；时间戳仅写此处与 `change_log[].time`，SVG/CSV/report 正文不含时间戳以保证确定性 |

## 4. change_log 规则

- 只增不改、保持顺序；`bootstrap` 落一条、每次 `update` 指令落一条、失败落
  `update_error`、`finalize` 落校验结果。
- 每条必须写 `action`（做什么）+ `detail`（改动摘要）+ `reason`（为什么），
  使“先落盘、再改进”的过程全程可复核。

## 5. 校验规则（`validate`）

- `case_id` 非空、`status` 属于四态；
- `contract.fields` 中的每个键都必须以顶层键存在；
- `results` / `sources` / `uncertainty` / `change_log` 必须存在且类型正确；
- 任何校验问题写入 `runtime.problems` 与 `errors`，但**不删除已有合法内容**。