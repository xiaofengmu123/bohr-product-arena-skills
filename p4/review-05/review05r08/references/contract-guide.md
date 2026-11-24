# 契约与证据溯源指南（contract & evidence guide）

本文件说明脚本如何读写契约、如何建立证据索引、如何增量更新，以及如何保证同一输入重跑结论一致。

## 1. 输出契约回填（output_contract）

- 从 `/input/case.json` 的 `output_contract`（对象）读取原始字段；运行时 `result.json` 必须原样保留**全部字段**。
- 缺值给 `null`（列表类键名给 `[]`）；不改名、不删键、不新增无依据的键。
- 结构保持不变（嵌套对象递归回填），同时把所有叶子字段平铺到 `result.json` 顶层（不覆盖 `case_id/status/report/…` 核心键）。
- 若 `case.json` 没有 `output_contract`，写 `contract_missing: true`，`output_contract` 置空对象，并在 `degradations` 说明。
- 若 `case.json` 提供 `review_facets`/`categories`/`finding_types`/`severities`，作为动态 facet 覆盖默认值，写入 `allowed_*` 与 `review_facets`。

## 2. 证据索引（evidence.json）

`scripts/extract.py` 生成 `/app/work/evidence.json`，字段：

- `summary.files`：输入文件清单（path/rel/role/size/text_available）。
- `summary.errors` / `summary.degradations`：解析失败与降级记录。
- `index.<rel>.lines`：逐行 `{n, t}`（规范化后的逐字文本），供定位与引用。
- `signals.<rel>`：六个方向的**待查信号**（章节、引用标记、图/表/代码、隐私、统计、伦理）及行号。

PDF 必须先用平台的 `pdf-text-tool`（或 `pdftotext`）解析到 `/app/work/parsed/<原名>.txt`，再重跑 `extract.py` 重建索引；**只覆盖已解析文本，不猜内容**。索引是候选观察，不是结论。

## 3. 增量工作流（change_log 可复核）

1. `scripts/main.py`（run 子命令）先落盘 `result.json` 骨架（status=partial）+ `report.md` + `plan.md`，此后任何中断都有合法产物。
2. Agent 逐条判定后把 finding 写 `/app/work/findings.json`（列表或 `{"findings": [...]}`）。
3. `scripts/main.py merge --findings /app/work/findings.json` 按 `id` 幂等 upsert（后写覆盖前写），自动更新 `checked_items`/`not_checked`/`pending_review` 并追加 `change_log`，同时重新生成 `report.md`。
4. `scripts/main.py finalize --status ok --summary "..."` 设置最终状态与摘要。
5. `scripts/validate.py` 独立重建索引并逐条核验 quote，FAIL 必须修复。

## 4. 确定性

- 文件遍历按字典序固定；正则固定；`default_leaf` 规则固定；`flatten_leaves` 固定。
- `merge` 按 id 排序输出，`change_log` 追加 UTC 时间戳（时间戳仅影响日志，不影响 finding 集合）。
- 同一输入 + 同一 parsed 文本重跑，finding 集合与 `quote` 核验结果一致。

## 5. 降级与局限（不编造）

| 情形 | 处置 |
|---|---|
| 空输入目录 | 退出码 0，`status=insufficient_evidence`，`degradations` 说明 |
| 坏 JSON / 坏编码 | 记入 `errors`/`degradations`，继续处理其余材料 |
| 单文件 >2MB | 跳过并记录，不读取 |
| PDF 无法解析 | 记 `degradations`，只覆盖已解析文本；无法判断标 `pending_human` |
| 缺 `output_contract` | 用标准 schema，`contract_missing=true` |
| 缺软件/工具 | 记 `degradations`/`limitations`，不伪造运行结果或评测分数 |
| 受限数据 | 只标位置与类型，不复述、不扩散，标 `pending_human` |

任何未完成项都如实写进 `report.not_checked` 或 `pending_review`，不得用示例填充冒充正式结果。
