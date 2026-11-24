# 归一化、去重与增量判定规则

本文件定义 Skill 的确定性规则。所有脚本只做确定性计算；实质检索与判定由 Agent 完成并写入 `/app/work/findings.json`。

## 1. 去重键（优先级从高到低）

对每条候选文献计算一个稳定的 `canonical_id`，用于跨源归并：

1. **DOI**（归一化后，前缀 `doi:`）：`10.1000/XYZ`。
2. **arXiv 编号**（归一化后，前缀 `arxiv:`）：`1901.01234`。
3. **归一化标题**（前缀 `title:`）：全小写、NFKC、标点转空格、空白折叠。

同一条文献同时给出 DOI 与 arXiv 时，DOI 优先；两条记录仅在都无 DOI 时才用 arXiv 归并，再退到标题。标题键只兜底，不用于判定"确为同一篇"，仅标为候选合并。

## 2. 归一化规则

| 字段 | 规则 |
|-|-|
| DOI | 去 `https://doi.org/` / `dx.doi.org/` 前缀、去 `doi:` 前缀、小写、NFKC、去首尾标点与空白 |
| arXiv | 去 `arXiv:` 前缀、小写、去版本后缀 `vN` |
| 标题 | 小写、NFKC、非字母数字下划线字符全部转空格、连续空白折叠 |

标题归一化**不**删除副标题、不拼写纠错、不翻译：避免把不同文献误并。差异超出规则范围时保留为独立条目并在 `uncertainty` 标注。

## 3. 版本链与合并

同一 `canonical_id` 下的多条记录按 `(date, version/source)` 稳定排序，构成版本链：

- **预印本 → 正式版**：同为一条文献，正式版元数据作为主记录（primary），预印本保留在 `versions`。
- **勘误 / 更正（correction/erratum/corrigendum）**：记入 `markers`，作为 `updates` 而非 `new`。
- **撤稿（retraction/retracted/withdraw[n]）**：从 primary 候选排除，单独进 `retractions`，不得作为新发现推送。

primary 选取：优先选非撤稿记录；多条非撤稿记录选日期最新者。排序键固定，重复运行结果一致。

## 4. 增量判定（快照比对）

`snapshot` 保存上一窗口的 `canonical_id` 集合（可带水位时间）。对本窗口候选分组后：

- `new_items`：`canonical_id` 不在快照中。
- `updates`：在快照中且本窗口出现新版本/更正（`versions` 长度 > 1 或带 correction/erratum marker）。
- `retractions`：带撤稿 marker。
- `unchanged`：其余。

同窗口、同快照、同来源 → 分类结果唯一（确定性）。

## 5. 时间窗

- 窗口为**闭区间**且相邻窗口首尾相接，避免缝隙造成漏检或重复覆盖。
- 全部转 UTC ISO-8601；边界条目的日期缺失时记 `not_reported` 并标 `uncertainty`，不猜测。
- 延迟入库条目：若其出版/入库时间落回已覆盖窗口，按快照水位补回，不二次推送。

## 6. 提醒抑制

只推送真 `new`、版本推进与撤稿/更正；以下一律抑制：

- 转载/镜像：同一 `canonical_id` 只推一次。
- 刚被索引的旧文献（日期落在已覆盖窗口）：抑制。
- 重复条目：去重后唯一化。

## 7. 失败重试与幂等

- 限流/超时/来源改版：按指数退避重试（Agent 侧），重跑幂等——同一去重键不会二次进入 `reminders`。
- 任何一次增量可由 `subscription + window + snapshot + dedup` 记录复算。

## 8. 订阅模板与快照样本

`subscription` 至少含查询式、来源、更新频率；`snapshot` 存上一窗口 canonical_id 集合与水位。示意（均为示例，非正式案例结果）：

```json
{
  "subscription": {
    "topic": "示例主题（以材料实际研究问题为准）",
    "query": "（从材料抽取的检索式）",
    "sources": ["Crossref", "EuropePMC", "publisher"],
    "frequency": "weekly"
  },
  "window": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-08T00:00:00Z"},
  "snapshot": {
    "canonical_ids": ["doi:10.1000/xyz", "arxiv:1901.01234"],
    "watermark": "2026-01-01T00:00:00Z"
  }
}
```

## 9. 回放步骤（任意一次增量可复算）

1. 读取 `/app/work/findings.json` 中的 `subscription`、`window`、`snapshot`。
2. 用相同来源重跑窗口检索（或重新解析材料），得到候选 `records`。
3. `python3 scripts/main.py dedup --records /app/work/records.json --out /app/work/dedup.json` 复算 `canonical_id` 与版本链。
4. 用 `snapshot.canonical_ids` 对去重结果求差，复算 `new/updates/retractions`。
5. 与已记录分类比对；一致即回放成功，不一致即定位差异并记 `source_conflict`。

## 10. 工作示例（示意，非正式案例）

三条记录：预印本（arXiv 1901.01234v1，无 DOI）、正式版（DOI 10.1000/xyz，同标题）、更正（同 DOI，标题含 "Correction"）。去重后：

- `canonical_id`：DOI 版与更正归并为 `doi:10.1000/xyz`（DOI 优先）；预印本若无 DOI 则单独 `arxiv:1901.01234`。
- 版本链 `versions` 按日期排序；primary 选最新非撤稿记录。
- 更正带 `correction` marker → 归入 `updates` 而非 `new`；若快照已含该 DOI，则只推更新，不重复推新增。

若预印本与正式版同文，由 Agent 依据材料中的关联证据（如 Crossref relation / 标题完全一致）在 `findings.json` 里显式合并并留 `evidence`（locator + quote）；脚本不自动把不同键的条目合并。
