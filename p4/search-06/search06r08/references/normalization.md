# 归一化、去重、版本链、时间窗与提醒规则

本文是 SKILL.md 的领域规则补充，运行前先读。所有规则均实现于
`scripts/core_impl.py`，纯函数、确定性、可复算。

## 1. 稳定去重键（优先级从高到低）

对每条候选文献计算 `canonical_key`，只取第一个命中：

1. **DOI**（首选）：`normalize_doi` 去 `https://doi.org/` / `doi:` 前缀、
   去首尾标点与引号、转小写、去空白。无法识别为 DOI（无 `/` 或过短）则放弃，
   转下一键。
2. **arXiv**：`normalize_arxiv` 去 URL/`arXiv:` 前缀、去 `vN` 版本后缀、转小写；
   只接受新式 `YYYY.NNNNN` 或旧式 `cat/NNNNNNN`。
3. **归一标题**（兜底）：`normalize_title` 做 NFKC、转小写、非字母数字转空格、
   合并空白。仅作兜底，同文多源（DOI 与 arXiv 并存）以 DOI 为准。

规则：同一 DOI 的不同大小写/标点记为同一条；DOI 与 arXiv 并存时按 DOI 归一；
无任何稳定键的记录进入 `unkeyed_records`，绝不静默丢弃。

## 2. 版本阶段分类（`classify_version`）

优先级：`retraction` > `correction` > `preprint` > `journal` > `unknown`。

- 文本命中 retract/retracted/retraction/withdrawn → `retraction`；
- 命中 erratum/corrigendum/correction/corrected/addendum → `correction`；
- 命中 preprint/arxiv/working paper/pre-print → `preprint`；
- 有 DOI 且有期刊/container-title → `journal`。

分类依据只能是**材料中的实际文本字段**（type/version/subtype/title/note/
status/message/content/abstract），不依据文件名或领域标签臆测。

## 3. 版本链（`build_version_chains`）

按归一标题聚合同一工作的不同阶段（预印本与正式版通常同标题而 DOI 不同）。
链内按 `preprint → journal → correction → retraction` 排序，`stages`
记录各成员阶段；当链内阶段数 > 1 时为 `is_version_chain=true`。
版本推进只在这条链上判定：预印本→正式版是 `update`，正式版→勘误是
`update`，撤稿/更正与新增**分开**记录。

## 4. 时间窗（闭区间，UTC）

- 窗口为闭区间 `[start, end]`，UTC 归一；`parse_date` 兼容 ISO 8601（含时区）、
  `YYYY-MM-DD`、`YYYY/MM/DD`、Crossref `date-parts`、纯年份。
- 边界命中（start/end 当天）算窗口内；`in_time_window` 返回值语义：
  `True` 在窗内、`False` 在窗外、`None` 日期或窗口缺失。
- 无缝推进：下一次窗口 start = 上一次窗口 end；窗口之间不重叠、不留缝。
- 补抓/回溯：入库滞后的旧文献（出版日期早于窗口但本次才被索引）进入
  `late_indexed`，**不作为新发现提醒**，只在记录中留痕。

## 5. 增量判定（`diff_snapshot`）

输入：候选 `search_results`、上一快照 `snapshot.canonical_ids`（已见稳定键
集合）、当前 `window`。输出五桶：

| 桶 | 判定 | reason 示例 |
|-|-|-|
| `new_items` | 未见于快照且出版日在窗口内 | in_window_new |
| `updates` | 快照已见工作的勘误/更正 | correction_of_seen_work |
| `retractions` | 撤稿/撤回声明 | retraction_notice |
| `late_indexed` | 出版日早于窗口的旧文献 | late_indexed_old_item |
| `suppressed` | 已见重复 / 日期或窗口缺失 | already_seen_duplicate |

## 6. 提醒合并与抑制（`build_reminders`）

只把三类推给用户：`retractions`、`updates`、`new_items`。规则：

1. 同一稳定键在一次运行内只推一次（`duplicate_notification` 抑制）；
2. `late_indexed` 一律抑制，理由 `late_indexed_old_item`；
3. 已见重复条目抑制，理由 `already_seen_duplicate`；
4. 转载/同文多源已被 DOI 归一折叠，不再重复推送。

抑制本身要有理由，写入 `suppressed`，可审计。

## 7. 失败重试与幂等

- 脚本不做网络请求，不存在限流/改版重试的副作用；Agent 层面的重试要求
  幂等：同一请求失败重发，不产生二次推送（提醒去重键兜底）。
- 写盘用原子替换（`write_*_atomic`），中途崩溃不留下半截文件。
- 同一输入两次运行字节一致（`json.dumps(sort_keys=True)`）。

## 8. 版本记录（可回放性）

`change_log` 记录每次事件（最早 `skeleton_written`，随后 `finalized`、
`self_check_passed`）；`tracking` 记录 `subscription`、`window`、`snapshot`、
`dedup_groups`、`version_chains`、`reminders` 与 `suppressed`。相同快照 +
相同窗口 + 相同来源 → 相同 `new/updated/removed`，可独立复算。
