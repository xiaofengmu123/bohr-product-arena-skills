# 去重归一与增量判定规则

运行 `python3 scripts/main.py dedup` 前先读本文件。所有规则都要求**可回放**：同一批 records + 同一快照 + 同一窗口 → 相同的 `new/updated/removed` 与提醒列表。本包脚本只做确定性部分，实质判定由 Agent 完成并写进 `findings.json`。

## 1. 归一化（normalize）

去重前先归一，输入值一律转字符串、去首尾空白。

| 字段 | 规则 | 示例输入 → 输出 |
|-|-|-|
| DOI | 去 `https://doi.org/`、`http://dx.doi.org/`、`doi:` 前缀；去尾部 `.,;:`；转小写；无 `/` 判非法 | `https://doi.org/10.1000/XYZ.` → `10.1000/xyz` |
| arXiv | 匹配 `arxiv:`/`arxiv.org/abs/`/裸编号；取编号本体；**去 `vN` 版本后缀**（版本留作版本链） | `arXiv:2401.00001v2` → `2401.00001` |
| 标题 | 去 HTML 标签；NFKD 折叠重音；小写；非字母数字替换为空格；压缩空白 | `The  Same-Paper!` → `the same paper` |

`canonical_keys(record)` 对一条记录返回它拥有的全部键，优先级 `DOI > arXiv > 标题`；`canonical_id` 取最高优先级键生成可读 id：`doi:…`、`arxiv:…` 或 `title:<sha1-20>`。

## 2. 去重（union-find）

- 以“任一共享归一键”合并记录：两条记录只要 DOI、arXiv 或归一标题有一个相同就归为同一簇。这解决三类常见重复：
  1. 同一工作一源有 DOI、一源只有标题；
  2. DOI 与 arXiv 并存、大小写/标点差异；
  3. 同文多源（出版社 + Crossref + EuropePMC 转载）。
- 簇内选代表：`formal(有 DOI/期刊) > preprint > 无 DOI`；撤稿/更正记录不选为代表。
- 无任何归一键的记录进 `orphans`，不强行合并。

## 3. 版本链（version chain）

同一簇内按标题/类型/期刊文本分类：

- **retraction**：标题或类型含 `retract/retracted/retraction`；
- **correction**：含 `erratum/corrigendum/correction/addendum`；
- **preprint**：含 `preprint/arxiv/biorxiv/medrxiv/chemrxiv/ssrn` 且无期刊；
- **formal**：有 DOI 或期刊。

`preprint → formal` 记为 `update`（版本推进），不是两条 `new`；撤稿/更正单列到 `tracking.retractions`，不与新增混在一起。

## 4. 时间窗（window）

- 起止均为闭区间 `[start, end]`，UTC 归一（ISO-8601 + `Z` 或 `+00:00`）。
- 相邻窗口首尾相接、不重叠、不留缝；上一窗口 `end` 的次日 00:00:00Z 即本窗口 `start`。
- 边界条目按**入库时间**归窗；入库时间缺失时按发布日期归窗并标 `uncertainty`（字段 `date_assumed`）。
- 延迟入库（本窗口才被索引的旧文献）单独标 `late_indexed`，**不推为 `new`**，避免误报。

## 5. 快照与增量（snapshot / diff）

- `snapshot` 存上一窗口结束时已见的 `canonical_id` 集合（按类型分桶：`doi/arxiv/title`）。
- 本窗口候选与快照求差：
  - `new_items`：本窗口出现、快照中没有的 id，且非 `late_indexed`；
  - `updates`：id 已存在但版本推进（如 preprint→formal、v1→v2）；
  - `retractions`：出现撤稿/更正标记的记录；
  - `removed`：快照有、本窗口来源不再返回的 id（注明“可能是收录波动”，不下断言）。
- 同一 id 的转载/重复只保留一个提醒（代表 + `duplicates_suppressed: n`）。

## 6. 提醒抑制（reminders）

只对以下情况投递提醒：

1. 真 `new`（确属本窗口新入库，且非转载、非刚索引旧文献）；
2. 版本推进（preprint→formal、vN→vN+1）；
3. 撤稿/更正。

抑制：转载、旧文献刚被索引、同簇重复、快照已含 id、提醒已投递过（幂等）。每次投递记录 `delivery_log`（窗口、id、类型、时间、去向），重复运行不二次推送。

## 7. 失败重试（retry）

- 限流/超时：指数退避重试（等间隔递增），可恢复、幂等；仍失败记录真实错误，不伪造响应。
- 来源改版/字段缺失：字段缺失记 `null`/`not_reported`；来源改版记 `degradations`，用可用字段降级。
- 所有重试不得产生二次推送：以 `delivery_log` 去重。
