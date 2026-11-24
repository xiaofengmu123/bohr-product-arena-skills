# 证据溯源强化规范（evidence_map_v2）

本文件是 SKILL.md 第 5、6 节的执行细则，是“证据溯源强化”变体的核心规范：每条结论都必须带
`locator` 与逐字 `quote`，并由 `finalize` 自动回核；`evidence_map` 采用比普通
`file+sha256+locator+quote` 更细的双向结构。

## 1. locator 规范

| 文件类型 | locator 形式 | 说明 |
|---|---|---|
| 文本/Markdown/JSON/CSV | `materials/paper.md#L12-L15` | 单行写 `#L12`；跨行用 `#L12-L15` |
| PDF | `materials/paper.pdf#p3` | 优先页码；无文本层时不写页码，只给 `file+sha256` 并标 `unreadable/needs_ocr` |
| 无法定位 | `materials/x.bin#unknown` | 附 `pending_human`，不编造坐标 |

`locator` 必须同时配合 `file`（相对 `/input` 的路径）与 `sha256`（内容哈希）使用；
三者共同构成可独立复核的回链坐标。`char_span`（字符偏移）由 `finalize` 回核时自动补填，
人工写作阶段可留空。

## 2. 逐字 quote 与确定性哈希

- `quote` 必须是原文逐字片段，禁止改写、省略号拼接或释义替代。
- `quote_sha256` 是对 `quote` 规范化（小写 + 压缩空白 + 去首尾空白）后的 SHA256，
  用于跨环境核对“引文未被改动”。
- `quote_norm` 记录规范化结果，供复核人直接比对。

## 3. evidence_map_v2 结构（双向、细粒度）

```json
{
  "schema": "evidence_map_v2",
  "claims": [
    {
      "claim_id": "C001",
      "question": "问题原文",
      "claim": "答案中的关键论断",
      "status": "supported | conflict | insufficient_evidence | unreadable",
      "confidence": "certain | likely | conflict | insufficient_evidence | unreadable",
      "evidence_refs": ["E001"],
      "missing": null
    }
  ],
  "evidence": [
    {
      "evidence_id": "E001",
      "file": "materials/paper.md",
      "sha256": "...",
      "locator": "materials/paper.md#L12-L15",
      "char_span": [10, 45],
      "quote": "逐字片段",
      "quote_norm": "规范化片段",
      "quote_sha256": "...",
      "confidence": "certain",
      "source_role": "primary | supporting",
      "used_by": ["C001"],
      "verification": {
        "checked": true,
        "status": "verified | unverified",
        "found_locator": "materials/paper.md#L12-L15",
        "note": null
      }
    }
  ],
  "conflicts": [],
  "pending_human": []
}
```

与单层证据列表相比，v2 的差异点：

1. **claim ↔ evidence 双向**：每条 claim 有 `evidence_refs`，每条 evidence 有 `used_by`，
   反向追踪“同一片段支撑了哪些结论”无需重建索引。
2. **粒度坐标**：`char_span` 补充行号之外的字符级定位，长行引用可精确到字符。
3. **引文防篡改**：`quote_sha256` + `quote_norm` 使“quote 是否被改写”可机械核验。
4. **来源角色**：`source_role` 区分 `primary`（直接支撑）与 `supporting`（佐证/背景），
   使答案的权重可读。
5. **自动回核**：`verification` 字段由 `finalize` 自动填充，记录该 quote 是否在原文件中
   真实命中，并回写 `found_locator`；对不上即标 `unverified` 并写 `note`，不静默通过。

## 4. 不确定性分级

| 级别 | 定义 | 处置 |
|---|---|---|
| `certain` | 逐字 quote + 单一来源 | `confidence=certain`，`source_role=primary` |
| `likely` | 由原文推断或摘要，非逐字 | 明确写“推断依据”，不给伪 quote |
| `conflict` | 同一指标多来源不一致 | 全部记录，标 `source_conflict`，写入 `conflicts` |
| `insufficient_evidence` | 库内无依据 | 敢说“没有”，写明缺什么，写入 `pending_human` |
| `unreadable` | 不可读/扫描件无文本层/权限不足 | 标 `unreadable/needs_ocr`，不编造文本 |

## 5. 确定性约定（复跑一致）

- SHA256 小写十六进制；文件遍历、重复组、版本家族、证据列表均按稳定键排序。
- 引用规范化规则固定为“小写 + 压缩空白 + 去首尾空白”，哈希算法固定 SHA256。
- `runtime.deterministic_fingerprint` 对 case_id + 排序后的文件(path,sha256) + answers
  求 SHA256，供复核人确认“同一输入 → 同一指纹”；墙钟时间不进入指纹。
