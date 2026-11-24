# 归一化、去重、时间窗与提醒规则（精确定义）

本文是 SKILL.md 第 5 节的精确实现依据。脚本 `scripts/main.py normalize` 与 `scripts/main.py dedup`
按此执行；Agent 的人工判断也按此口径书写，保证重跑一致。

## 1. 规范化（normalize）

- **标题**：`NFKC` 归一 → Unicode casefold → 将标点与下划线替换为空格 → 折叠连续空白 → 去首尾空格。
  例：`"A Study of Things?"` 与 `"a study  of things"` → `"a study of things"`。
- **DOI**：去掉 `doi:` 与 `https://doi.org/` 前缀、去首尾标点、转小写，取 `10.xxxx/...` 主段。
- **arXiv**：抽取新式 `2401.01234v2`（base 不含 `v`）与旧式 `cs.CL/0501234`；canonical 身份用 base，
  版本号保留在记录的 `arxiv_full` 中用于版本链。
- **日期**：归一为 UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ`。**naive 日期一律按 UTC 处理并在
  `uncertainty` 注明**（若来源有明确时区线索则按来源时区转换）；缺失记 `null`/`not_reported`。

## 2. canonical key（去重身份）

优先级：`doi:<归一DOI>` > `arxiv:<base>` > `title:<归一标题>`。同一记录只取最高可用优先级作为身份键。

聚类采用**并查集**，规则：

1. 共享同一 DOI 的记录合并；
2. 共享同一 arXiv base 的记录合并；
3. 归一标题完全一致的记录合并（弱连接，用于把 DOI/arXiv 记录与仅标题记录串成版本链）。

**保守原则**：模糊标题匹配默认关闭（`fuzzy_title_match: false`）。只有当两个标题在归一化后完全一致
时才合并，避免把同名不同文误并；近重复仅由 Agent 以证据人工判定，并写入 `evidence`。

## 3. 版本链与分类

每组内记录按 `(日期缺失在后, 日期升序, 类别权重)` 排序，类别权重：

`retracted(0) < correction(1) < published(2) < preprint(3) < unknown(4)`。

- 记录类别由标记词判定：撤稿（`retract/withdrawn/撤稿/撤回`）、更正（`correction/erratum/
  corrigendum/addendum/勘误/更正/修订/revised/updated`）、有 DOI → `published`、有 arXiv → `preprint`。
- 组类别 = 组内出现的最高优先级类别（撤稿 > 更正 > 正式版 > 预印本 > 未知）。
- 版本链输出为组内记录的有序列表，保留每条记录的 `_canonical_key`、`_date_norm`、`_record_class`，
  供回放。

## 4. 时间窗与增量判定

- 窗口为**闭区间** `[start, end]`，UTC 归一，相邻窗口 `end_{k-1} = start_k`（无缝）。
- 记录日期落在 `[start, end]` 内才计为“本窗口新增”；落在窗口外但被本窗口抓到的条目标记为
  “延迟入库/旧文献”并按 `suppress_out_of_window` 抑制，不当作新发现推送。
- 快照 `snapshot` 存上一窗口的 `canonical_id` 集合与 `last_window_end`。增量判定：

  - canonical key 不在快照 → `new`；
  - canonical key 在快照且版本推进（如 preprint→published）→ `update`；
  - 组内出现撤稿标记且此前未标撤稿 → `retraction`；
  - 组内出现更正/勘误且此前未标 → `correction`；
  - 其余（转载、重复、窗口外旧文献）→ 抑制。

## 5. 提醒合并与抑制

- 只对 `new`、`update`、`retraction`、`correction` 生成提醒。
- 抑制：重复条目、转载（同文多源）、窗口外旧文献、无新信息的重复运行。
- 幂等：同一 `(snapshot, window, source)` 重跑，提醒集合完全一致；已投递的提醒不进 `reminders` 二次推送。
- 提醒内容只陈述事实（新增/版本推进/撤稿/更正 + 来源），不上升为科学结论。

## 6. 失败重试

- 可恢复错误（限流/超时/来源改版）：退避重试，记录每次尝试；仍失败如实写入 `errors`，不伪造响应。
- 不可恢复错误（无权限/材料缺失）：写 `permission_errors`/`insufficient_evidence` + `pending_human`。
- 所有重试对同一输入幂等；重试不得产生重复提醒。

## 7. 回放步骤（任意一次增量可复算）

1. 取 `plan.json` 中的 `subscription`、`window`、`snapshot`、`dedup`、`reminders` 配置。
2. 按 `window` 对来源执行相同查询/解析，得到 `records.json`。
3. `python3 scripts/main.py dedup --records records.json --out dedup.json`。
4. 用 `snapshot.canonical_ids` 求差，得到 `new/update/retraction/correction`。
5. `python3 scripts/main.py finalize --findings findings.json --dedup dedup.json`。
6. 相同输入必须产生相同的 `result.json` 主字段（时间戳除外）。
