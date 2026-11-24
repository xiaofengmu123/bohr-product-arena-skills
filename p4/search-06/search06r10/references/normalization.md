# 归一化、去重与增量判定规则

本文是 `scripts/core.py` 中确定性规则的书面说明，供 Agent 在检索判断时遵循，也供复核者独立复算。所有规则只依赖实际输入文件与原文，不依据领域标签臆测，不宣称实时联网覆盖。

## 1. 稳定去重键（canonical_id）

优先级固定为 **DOI > arXiv > 归一标题**，与脚本一致：

| 优先级 | 键类型 | 归一化规则 |
|---|---|---|
| 1 | `doi` | 去 `https://doi.org/`、`doi:` 前缀；整体小写；去尾部 `.,;`；取 `10.\d{4,9}/…` 匹配段 |
| 2 | `arxiv` | 去 `arxiv.org/abs/`、`arxiv:` 前缀；新格式归一为 `YYYY.NNNNN`（去 `vN`）；旧格式 `cat/ID` 去 `vN` |
| 3 | `title` | NFKD 去重音 → 小写 → 仅保留字母/数字/CJK（`ch.isalnum()`），去空白与标点 |

同文多源、DOI 与 arXiv 并存、转载条目都因键相同而合并为一组；组内保留成员数与合并前条数，`duplicates_suppressed = input_count - merged_count`。

## 2. 版本链与合并

每组（同 canonical_id）内所有成员按版本 token 归并：

- `preprint`（含 arxiv、working paper）< `journal`（含 published、final）< `correction`（含 erratum、corrigendum）< `retraction`。
- 代表元数据取稳定标识最丰富者：DOI(+4) > arXiv(+2) > title(+1) > 日期(+1)，同分取日期更晚者。
- `retracted` 为组级标志：任一成员为撤稿/撤回即置真。

**预印本→正式版→勘误→撤稿** 按 `version_chain` 升序记录；一次增量内同一文档只产生一条记录。

## 3. 时间窗

- 窗口为 UTC 闭区间 `[start, end]`，相邻窗口**无缝衔接**（上一窗口 `end` 等于下一窗口 `start`）。
- 边界条目按 `start <= t <= end` 判属本窗口；延迟入库条目（`indexed_at` 晚于窗口但内容日期在窗口内）须补回，并在 `evidence` 注明补抓原因。
- 日期缺失记 `not_reported`，不得推断；时区不一致一律转 UTC 后再比较。

## 4. 快照与增量判定

- `snapshot` 存上一窗口的 `{canonical_key: {versions, retracted}}`（由 `snapshot_of` 生成）或等价 `previous_ids` 集合。
- 增量 `diff_snapshot(current, snapshot)` 输出三组：
  - `new`：当前有、快照无；
  - `updated`：两侧都有，但版本链前进（`max_version` 增大）或由未撤稿变为撤稿；
  - `removed`：快照有、当前无。
- 任意一次增量可凭「相同快照 + 相同窗口 + 相同来源 + 相同去重键」复算，结果必须一致。

## 5. 提醒与抑制

只对 `new` 与 `updated`（版本推进/新撤稿）投递提醒；以下一律抑制：

- 转载/同文多源（已并入同 canonical_id）；
- 刚被索引的旧文献（内容日期早于窗口起点且不在 `new` 判定内）；
- 重复条目（去重后仅剩一组）；
- 无实质变化的重复抓取。

`reminders` 每条含 `key`、`kind`（`new|update|retraction`）、`reason`，可回放。

## 6. 失败重试与幂等

- 限流/超时/来源改版：指数退避重试，重试只补抓、不二次投递已发出的提醒。
- 写盘：原子写（临时文件 + `os.replace`），避免半写文件。
- 脚本确定性强约束：除 `runtime`/`change_log` 的时间戳外，领域字段（`search_results`/`tracking`/`dedup` 计数与内容）在相同输入下逐字节一致。

## 7. 证据溯源（硬要求）

每条候选、每个关键数值、每类判定（新/更新/撤稿）都要给 `locator + quote`：

- `locator`：如 `materials/paper.md#p3`、`case.json$.source`、PDF 页码；
- `quote`：材料中的**逐字片段**（可截断，但须原样）。
- 冲突信息两个都记，并标 `source_conflict`；超出材料可见范围的结论写入 `uncertainty`。

## 8. 降级

- 字段缺失 → `null`/`not_reported`，不推断。
- 材料不足以判定新/旧 → `insufficient_evidence` + `pending_human` 列缺口。
- 无 DOI/arXiv 且标题无法归一 → 记 `canonical_id.type = "none"`，不参与去重合并，单独保留并标注。
