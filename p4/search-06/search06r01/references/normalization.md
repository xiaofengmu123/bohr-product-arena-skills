# 去重、版本链、时间窗与提醒规则

运行前先读本文件；`scripts/core_impl.py` 是本文件的确定性实现。

## 1. 去重键（稳定键，优先级从高到低）

| 优先级 | 键 | 归一化规则 |
|-|-|-|
| 1 | DOI | 去 `https://doi.org/`、去 `dx.doi.org/`、小写、去首尾 `.,;` |
| 2 | arXiv | 去 `arxiv.org/abs|pdf/`、去 `arXiv:` 前缀、去 `vN` 版本尾、小写 |
| 3 | 归一标题 | 小写、`&` → ` and `、去标点（保留 `\w` 与空白）、折叠空白 |

- 同一 DOI 的不同写法（大小写、`https://doi.org/` 前缀、尾随标点）归为一条。
- DOI 与 arXiv 并存时以 DOI 为主键；arXiv 仅在没有 DOI 时作为主键。
- 同文多源（Crossref/EuropePMC/出版社各一条）在 `versions` 中保留来源，不重复推送。
- 无法得到任何键的记录标为 `unkeyed`，单独留待人工核对，不强行合并。

## 2. 版本链（预印本 → 正式版 → 更正/撤稿）

对每个 canonical_id 内的成员排序（日期优先，同日期按 version），并判定：

- `preprint`：`type=preprint`，或仅有 arXiv 而无 DOI。
- `published`：`type=published`，或同时有 DOI 与 journal。
- `correction`：`type ∈ {correction, corrigendum, erratum, corrected}`。
- `retraction`：`type ∈ {retraction, retracted}` 或 `status=retracted`。

组级 `kind` 取优先级：`retraction` > `correction` > `published` > `preprint` > `unknown`。
`latest` 取日期/版本最大的成员；`versions` 保留全部成员以支持回放。

## 3. 时间窗与增量判定

- 窗口为 UTC 闭区间 `[start, end)`，连续无缝；每次推进 `end_prev == start_next`。
- 快照存上一窗口的 canonical_id 集合（可附 last-known date），用于求差：
  - `new`：canonical_id 不在快照。
  - `updated`：在快照但成员数增加或 latest 日期/版本推进。
  - `retraction` / `correction`：组内出现撤稿/更正事件（优先于 new/updated）。
  - `unchanged`：在快照且无推进。
- 边界与延迟入库条目：把 `[end-δ, end]` 内的条目在下窗口首轮重查一次，避免漏检；δ 默认 1 天，可按材料约束调整。
- 任何增量判定都要写进 `evidence_map`：给出窗口起止、快照来源、逐字 quote。

## 4. 提醒合并与抑制

- 只推真 `new`、版本推进（`updated`）、撤稿/更正（`retraction`/`correction`）。
- 抑制：转载、旧文献（快照内 unchanged）、同 canonical_id 的重复成员、非窗口条目。
- 同一 canonical_id 只发一条提醒，多版本合并为"推进到 vX / 正式版 / 更正"。
- 提醒投递记录（提醒 id、时间、canonical_id、动作）写入 `tracking.reminders`，幂等：同一 canonical_id 同动作不二次投递。

## 5. evidence_map 结构（证据溯源强化，本版重点）

`result.json` 顶层 `evidence_map` 按 claim_id 键控；每个结论一条，结构如下：

```json
{
  "claim": "该结论的一句话",
  "subject": "关联的 record_id 或 null",
  "kind": "new|update|retraction|correction|dedup|window|query|ranking|metadata|absence|inventory",
  "source_id": "sources 中对应条目的 id（可空）",
  "locator": "精确位置，如 materials/xxx.json#/items/0 或 paper.pdf#page=3",
  "quote": "逐字原文（不得改写）",
  "quote_start": 0,
  "quote_end": 42,
  "quote_hash": "sha1(quote) 十六进制，供复核",
  "basis": "说明该片段为何支持此结论",
  "confidence": "established|inferred|uncertain",
  "conflicts": [
    {"locator": "...", "quote": "...", "source_id": "...", "basis": "为何冲突"}
  ]
}
```

硬要求：

1. 每条结论必须 `claim` + `locator` + `quote`；`kind ∈ {absence, not_reported}` 时允许 `quote` 为空，但必须给 `basis` 说明为何无逐字文本。
2. `quote` 必须逐字来自材料，禁止改写；`quote_hash` 由 `scripts/main.py` 在落盘时校验。
3. 冲突信息不隐藏：同一指标多来源不一致时全部写入 `conflicts`，每个冲突也要带 `locator` + `quote`。
4. 字段缺失记 `null`/`not_reported`，不推断；`confidence` 只能取三个值之一。

## 6. 失败重试与幂等

- 限流/超时/来源改版：可恢复错误幂等重试（同请求、同窗口、同快照重跑结果一致），不二次投递。
- 重试后仍失败：写 `errors` + `pending_human`，不伪造响应、不虚构文献。
- 写盘失败：降级到最小合法 JSON（`case_id` + `status` + `errors`），进程退出码 0。

## 7. 最小调用示例（示例，非正式案例）

### 7.1 去重与版本链

输入 `records.json`：

```json
[
  {"record_id":"r1","doi":"10.1000/XYZ","title":"A Paper","type":"preprint","arxiv":"2101.00001"},
  {"record_id":"r2","doi":"10.1000/xyz","title":"A Paper","type":"published","journal":"J","published_date":"2024-03-01"},
  {"record_id":"r3","title":"A Paper","type":"erratum","doi":"10.1000/XYZ"},
  {"record_id":"r4","title":"B Paper","arxiv":"2101.00002v2"}
]
```

```bash
python3 scripts/main.py dedup --records /app/work/records.json --out /app/work/dedup.json
```

结果（要点，示意）：r1/r2/r3 归入 `doi:10.1000/xyz` 一组，版本链为 preprint→published→erratum，`duplicate_count=2`、`kind=correction`；r4 独立为 `arxiv:2101.00002`。

### 7.2 一条完整 evidence_map 结论

```json
{
  "claim": "r2 是 r1 的正式版推进，属本窗口 updated",
  "subject": "r2",
  "kind": "update",
  "source_id": "s2",
  "locator": "materials/crossref.json#/items/2",
  "quote": "\"journal\":\"J\",\"published_date\":\"2024-03-01\"",
  "quote_start": 40,
  "quote_end": 82,
  "quote_hash": "sha1(逐字原文) 的十六进制",
  "basis": "同一 DOI 的 preprint 成员后出现 journal+date，版本推进到正式版",
  "confidence": "established",
  "conflicts": []
}
```

`quote_start`/`quote_end` 是逐字片段在源文件中的字符偏移，供他人精确回放；`quote_hash` 由 `scripts/main.py finalize` 校验，与原文不符会写入 `errors`。

