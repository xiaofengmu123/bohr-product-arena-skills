# 去重、版本链、时间窗与提醒规则

本文件是 `scripts/main.py` 确定性逻辑的规范说明，阶段 3/4 执行前必读。所有字段名以磁盘实际文件为准；本节描述默认键名，材料不一致时以材料为准并记 `degradations`。

## 1. 去重键（稳定、可复算）

优先级从高到低：

1. **DOI**：匹配 `10.XXXX/...`；大小写归一、去首尾空白与 `.,;`、剥离 `https://doi.org/`、`https://dx.doi.org/` 前缀。最终键形如 `doi:10.1000/xyz`。
2. **arXiv**：剥离 `arxiv:` 与 `arxiv.org/abs|pdf/` 前缀、去版本后缀 `vN`。键形如 `arxiv:2101.00001`（v1/v2 归为同一键，版本关系由版本链保留）。
3. **归一标题**：小写、去 HTML 标签、仅保留字母数字（含 Unicode 字母）、连续空白合并。键形如 `title:a paper`。

同一记录若有 DOI 与 arXiv 并存，取 DOI 优先；只有标题时用标题键（召回优先，版本链再合并）。

归一化可用 `python3 scripts/main.py normalize --record '{...}'` 单条验证，批量用 `dedup`。脚本返回 `_canonical_key` 与 `_canonical_kind`，同一键的条目视为同文多源/多版本。

## 2. 版本链与状态判定

对同一 canonical_key 的条目组：

- 按 `date`/`published`/`published_date`/`indexed_date`/`updated_date` 排序，最晚者为 `latest`。
- 标记 `flags`：包含 `retract/withdrawn/withdrawal` → `retraction`；包含 `correction/erratum/corrigend` → `correction`。
- 统计 `preprint_count`（type/status/note 含 `preprint`）与 `published_count`（type 含 `published` 或 status 含 `version_of_record`）。
- `merged=true` 表示同键多条（预印本→正式版→勘误应合并为一条追踪记录，不同状态分列 `versions`）。

**判定口径**：只有相对上次快照**新增**、**版本推进**（version/date/updated_date 变化）或**撤稿/更正**才提醒；转载、同文多源、刚索引旧文献一律抑制。

## 3. 时间窗

- 闭区间、连续无缝，统一归一为 UTC（ISO 8601）。窗口不得漂移；相邻窗口首尾相接。
- 跨窗边界与延迟入库条目：`date`/`indexed_date` 落在上一窗口但本轮才出现的，记 `degradations` 并补入当前窗口 `new_items`，不重复覆盖。
- 日期缺失 → `not_reported`；时区错位 → 记录原始值与归一后值，两者都进 `uncertainty`。

## 4. 提醒合并与抑制

- 只推送三类：真 `new`、版本推进、撤稿/更正。
- 抑制：同一 canonical_key 的重复条目、转载条目、旧文献（已在上次快照且无版本/状态变化）。
- 信息密度：同一主题合并为一条摘要，附去重键、窗口与证据链接，避免逐条刷屏。
- 提醒投递记录（时间、对象、是否抑制、原因）全量写入 `tracking.reminders`，可回放。

## 5. 失败重试与幂等

- 限流/超时/来源改版：指数退避重试，同一 canonical_key 不产生二次提醒；重试失败记录原始错误，不伪造响应。
- 字段缺失/日期缺失/时区错位：记录为 `degradations`/`not_reported`，不阻断其余条目。
- 幂等性：相同 `snapshot` + 相同窗口 + 相同来源 → 相同 `new/updated/removed`；`scripts/main.py dedup` 与 `diff_increment` 为纯函数。

## 6. 回放步骤

1. 读 `result.json` 的 `tracking.subscription`、`tracking.window`、`tracking.snapshot`。
2. 重跑 `scripts/main.py dedup --records ... --out ...` 复现去重键与版本链。
3. 用相同 snapshot 调 `diff_increment` 复算 new/updated/removed。
4. 逐条核对 `evidence` 的 locator + quote 是否指向材料原文。
5. 对照 `budget.summary` 核验阶段时间/请求预算是否按 §4（SKILL.md）执行。

复算结果与记录不一致时，标 `source_conflict` 并保留两方原始数据，不覆盖。
