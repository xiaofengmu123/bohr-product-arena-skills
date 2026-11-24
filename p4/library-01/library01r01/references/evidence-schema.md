# 证据溯源规范（evidence_map schema）

本包相对常规"文件整理"方案的取舍是 **证据溯源强化**：不是只在结果里给一个
扁平 `sources` 数组，而是把每条结论都落到一个细粒度的 `evidence_map`，且每条
结论至少带一个 `locator + 逐字 quote`。判断依据可独立复核，冲突不隐藏。

## 1. 结构与字段

`result.json` 顶层同时保留两个视图：

- `sources`：扁平视图，供快速检索；每项 `{item, locator, quote, role}`，
  其中 `item` 是所属结论的 `id`。
- `evidence_map`：结构化视图，`conclusions` 按类别键控，`id` 稳定可引用。

```jsonc
"evidence_map": {
  "schema_version": "1.0",
  "conclusions": {
    "dedup.0": {
      "id": "dedup.0",
      "claim": "按内容 SHA-256 判定 2 个文件重复",
      "strength": "established",
      "kind": "dedup",
      "method": "content_sha256",
      "evidence": [
        {"locator": "input/a.pdf", "quote": "sha256=…, 2 members", "role": "basis"}
      ],
      "uncertainty": null
    }
  }
}
```

## 2. 字段含义

| 字段 | 说明 |
|---|---|
| `id` | 稳定结论编号，前缀为类别（inventory/dedup/version/alias/version_note/reconcile/index/answer） |
| `claim` | 用一句陈述表达结论，强度由 `strength` 标注 |
| `strength` | `established`（机械可复现的事实）/ `preliminary`（启发式或初步推断）/ `inferred` |
| `kind` | 结论类别 |
| `method` | 判定方法，如 `content_sha256`、`normalized_stem + mtime + content_similarity` |
| `evidence` | 证据数组，每项含 `locator`、`quote`、`role` |
| `uncertainty` | 该结论具体的不确定来源，非"总有一句风险声明"的空话 |

## 3. locator 与 quote 的约定

- `locator` 是能指回源材料的定位：文件相对路径（POSIX 分隔符，如
  `input/data/report_v1.txt`）、`computed.inventory` 等机械证据定位，或
  目录清单中的记录定位。
- `quote` 是**逐字**片段：文本文件取首行原文（≤400 字符）、目录记录取该行
  原始 JSON/CSV 文本、版本说明取该行原文；二进制文件只提供 `hex` 预览并显式
  标注 `<hex-preview>`，不冒充逐字文本。
- 缺失写 `not_reported`，无法读取写 `<unreadable: …>`，**不编造哈希、DOI、
  数值或引用**。

## 4. 冲突与降级

- 同一指标两个来源不一致时，两条证据都记录，并在 `uncertainty` 标
  `source_conflict`；不擅自选一条"看起来对"的。
- 版本链为启发式：`strength=preliminary`，`uncertainty` 说明"按主干+mtime
  排序，非权威替代关系"，并把行政版本说明原文作为 `version_note` 结论附上。
- 无法取内容哈希的文件（`too_large`/`unreadable`/`non_regular`）单独成一条
  `inventory.skip.*` 结论，`quote` 记录原因，不静默丢弃。

## 5. 可复核性

任何结论都可按 `id → evidence.locator → 源文件 quote` 三步复核。审查者
不需要相信任何叙述性描述，只需要打开 locator 对照 quote 是否逐字一致。
