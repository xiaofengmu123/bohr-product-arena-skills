# evidence_map 结构与证据规范

本 Skill 的核心差异点是细粒度证据溯源：`result.json` 顶层 `evidence_map` 与独立文件
`/app/output/evidence_map.json` 同步记录每条结论的可复核证据。

## 1. 顶层结构

```json
{
  "evidence_map": {
    "version": "1.1",
    "count": 23,
    "claims": [ { "claim_id": "...", "...": "..." } ]
  }
}
```

- `version`：evidence_map 结构版本。
- `count`：claims 数量。
- `claims`：结论数组，每个元素是一"条"结论及其证据。

## 2. claim 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `claim_id` | string | 唯一编号，形如 `<claim_type>-NNNN`，如 `duplicate_group-0001` |
| `claim_type` | string | 结论类型：`duplicate_group` / `version_candidate` / `alias` / `version_note` / `catalog_record` / `reconciliation` / `answer` / `artifact_mapping` |
| `statement` | string | 用一句话陈述结论内容 |
| `conclusion_ref` | string | 指向 `computed` 中承载该结论的字段，如 `computed.duplicate_groups[0]` |
| `confidence` | string | `high` / `medium` / `low` |
| `heuristic` | bool | 是否由启发式得到（版本链、字段映射通常为 true） |
| `basis` | string[] | 支撑该结论的方法/依据，如 `["content_sha256"]`、`["stem_normalization","mtime_order","content_diff"]` |
| `sources` | object[] | 证据列表，见下 |
| `source_conflicts` | object[] | 冲突来源，同一指标不一致时全部保留并标注 |

## 3. source 字段

每条 source 至少包含：

| 字段 | 说明 |
|---|---|
| `locator` | 材料定位，见 locator 语法 |
| `quote` | 逐字原文或规范化测量串（禁止为空，除非确有具体依据） |
| `quote_sha256` | `quote` 的 SHA-256（UTF-8），用于复跑核对 |
| `evidence_type` | `verbatim` / `computed_derivation` / `derived_regex` / `parsed_field` |
| `role` | `subject`（被判定对象）/ `support`（辅助证据） |

### evidence_type 语义

- `verbatim`：quote 为材料中的**逐字原文**。
- `computed_derivation`：quote 为规范化测量串（如 `sha256:31ea…`、`dev:1;inode:42`、
  `stat:mtime=2024-01-01T00:00:00Z`）。这类结论没有"逐字原文"，用可复算的测量值代替，不冒充原文。
- `derived_regex`：quote 为正则命中的**原文片段**（如文件名中的 `_v2` 或版本说明行中的 `v2`）。
- `parsed_field`：quote 为结构化源文件中解析出的**字段原文**（如 catalog.json 的 `path` 字段值）。

## 4. locator 语法

| 形式 | 示例 | 说明 |
|---|---|---|
| 文件相对路径 | `materials/notes.md` | 相对 `/input` 的路径，统一用 `/` 分隔 |
| 行定位 | `materials/notes.md#L12` 或 `#L12-L15` | 第 12 行或第 12–15 行 |
| PDF 页 | `materials/paper.pdf#p3` | 第 3 页（若解析层提供页码） |
| JSON 字段 | `materials/catalog.json#records[0].title` | 数组下标或字段路径 |
| CSV 行 | `materials/list.csv#row=12` | 第 12 行 |
| BibTeX 条目 | `materials/refs.bib#entry=smith2024` | cite key |
| SQLite 表 | `materials/library.db#table=items` | 表名 |

## 5. confidence 与 heuristic 约定

- 去重（内容 SHA-256 相同）、对账匹配、符号链接/硬链接别名：`confidence=high`，`heuristic=false`。
- 版本链（归一化主干 + mtime 排序 + 内容差异）：`confidence=medium`，`heuristic=true`。
- 字段映射、作者/年份抽取：若仅从文件名正则得到，标 `heuristic=true` 且 `confidence=medium/low`，并写入 `uncertainty`。

## 6. 手工补充证据（--update 语义补丁）

Agent 在步骤 2 的语义结论可写成 patch 增量合并，不覆盖脚本已有证据。示例：

```json
{
  "status": "ok",
  "computed": {
    "version_candidates": [ { "stem": "report", "heuristic": true,
      "note": "人工确认 report_v2 替代 report_v1" } ]
  },
  "evidence_map": {
    "claims": [
      { "claim_id": "manual-0001", "claim_type": "version_candidate",
        "statement": "report_v2 替代 report_v1",
        "conclusion_ref": "computed.version_candidates[0]",
        "confidence": "high", "heuristic": false,
        "basis": ["human_review"],
        "sources": [
          { "locator": "materials/notes.md#L3", "quote": "版本说明：v2 替换 v1",
            "quote_sha256": "<复算值>", "evidence_type": "verbatim", "role": "subject" }
        ],
        "source_conflicts": [] }
    ]
  },
  "sources": [],
  "uncertainty": []
}
```

注意：`--update` 对 dict 递归合并、对 list 按 JSON 去重追加，不会删除既有键。手工
补丁若引用原文，`quote` 必须逐字、`locator` 必须可定位；不得补造 DOI、哈希或数值。
