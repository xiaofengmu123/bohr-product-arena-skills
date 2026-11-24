# 归一化、去重、增量与提醒规则

本文是 `SKILL.md` 第 5 节的细则，运行前先读。所有判定以实际输入文件和原文为事实依据。

## 1. 归一化键

| 键 | 归一化规则 | 示例 |
|-|-|-|
| DOI | 小写；去 `https://doi.org/`、`doi:` 前缀；去首尾空白 | `10.1000/XYZ` → `10.1000/xyz` |
| arXiv | 小写；去 URL/`arXiv:` 前缀；去版本后缀 `vN` 用于身份 | `arXiv:2103.12345v2` → `2103.12345` |
| 标题 | NFKD 去变音符后小写；非字母数字统一为空格；折叠空白 | `A Paper: Sub!` → `a paper sub` |

canonical_id 前缀分别为 `doi:`、`arxiv:`、`title:`；无法取键时用 `raw:<sha1前16>`。

## 2. 去重与版本链（union-find，确定性）

1. 相同 DOI → 合并（恒真）。
2. 相同 arXiv 基号 → 合并（恒真）。
3. 相同归一标题 → 桥接，**除非两条记录都带 DOI 且 DOI 不同**（同题异文不误并）。

组内代表取最高版本阶段（preprint < accepted < published < correction < retracted），`variants` 保留全部成员，`version_marks` 保留全版本签名，故预印本→正式版→勘误链可复算。标题桥接的已知边界：同名但无 DOI 的两篇独立作品可能被桥接，Agent 应据 quote 复核并在 `uncertainty` 标注。

## 3. 时间窗

- 闭区间 `[start, end)`，UTC 归一；`window.tz` 显式记录原时区。
- 相邻窗口首尾相接、不重叠不留缝；跨窗边界条目以入库时间归属唯一窗口。
- 延迟入库条目补回其真实入库窗口；跨窗未读条目记入 `updates` 而非丢失。

## 4. 快照与增量判定

- `snapshot.seen`：`{canonical_id: [version_mark, ...]}`。列表形式（仅 id 集合）视为无版本信息，不做 updated 判定。
- 相对快照：不在 `seen` → `new`；在 `seen` 且有新 `version_mark`（非列表形式）→ `updated`；阶段为 retracted → `retractions`；correction → `corrections`；其余 → `unchanged`。
- 判定只依据输入文件与原文，缺失即 `insufficient_evidence`/`pending_human`，不补造。

## 5. 提醒抑制

只推：真 `new`、版本推进（updated）、撤稿更正（retractions/corrections）。抑制：同 canonical_id 重复、转载/同文多源（已被 dedup 合并）、窗口外旧条目。投递记录（时间、条目、规则命中）写入 `tracking.reminders` 以便回放。

## 6. 失败重试与幂等

限流/超时/改版/字段缺失均重试且幂等：同快照+同窗口+同来源 → 同 `new/updated/retracted` 结果；不二次推送已投递提醒。重试仍失败则如实记 `degradations`，不伪造响应。

## 7. 回放步骤

1. 读 `tracking.subscription`（查询式/来源/频率）与 `tracking.window`（UTC 起止）。
2. 读 `tracking.snapshot.seen`（上一窗口 canonical_id + 版本签名）。
3. 用 `scripts/main.py dedup` 对候选去重，得到 canonical_id 与版本链。
4. 用 `scripts/main.py finalize` 对快照求差，得到 `new_items/updates/retractions/corrections`。
5. 逐条对照 `evidence[].locator` + `quote` 复核；`change_log` 提供每步事件与时间。
