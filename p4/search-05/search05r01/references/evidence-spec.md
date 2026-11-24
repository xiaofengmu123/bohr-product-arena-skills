# 证据溯源规范（evidence provenance spec）

本 Skill 的变体重点是**证据溯源强化**：每条关键结论都带 `locator + quote`，`evidence_map` 细分为五个可单独核对的面。本规范定义这些结构的精确语义，供复核者与自检脚本使用。

## 1. locator 语法

| 场景 | 形式 | 示例 |
|---|---|---|
| JSON 字段 | `<文件>#<JSON指针>:<记录内键路径>` | `crossref_1.json#/message/items/0:title` |
| JSON 记录容器 | `<文件>#<JSON指针>` | `europepmc_1.json#/resultList/result/0` |
| 文本/PDF 行 | `<文件>#L<行号>` | `notes.md#L12` |
| PDF 页（可选项） | `<文件>#p<页码>` | `paper.pdf#p3` |

JSON 指针遵循 RFC 6901：`~1` → `/`，`~0` → `~`。`:key_path` 部分用点分路径定位记录内字段（如 `journalInfo.journal.title`）。复用者用 `navigate_pointer` + `navigate_key_path`（`core_impl.py` 已实现）即可回源。

## 2. quote 逐字规则

- `quote` 是原始响应中该字段的**逐字取值**（`json.dumps(..., ensure_ascii=False)` 无损序列化），不做改写、转述、归一。
- `value` 是归一化后的规范值（如 year 从 `{"date-parts":[[2021]]}` 归一为 `2021`）；两者并列保存，便于区分「原文」与「解释」。
- `char_span` 是尽力定位的原文字符区间 `[start, end)`；无法定位时为 `null`，不影响 `quote` 的有效性。
- 对文档类材料，`quote` 取命中的整行文本（含 DOI/PMID 的原始行）。

## 3. evidence_map 五面结构

```json
{
  "claims": [
    {"claim_id": "c:1", "record_id": "rec:1", "field": "title",
     "value": "...", "evidence_id": "ev:2", "verdict": "asserted"}
  ],
  "field_sources": {
    "rec:1": {"title": ["ev:2"], "year": ["ev:3", "ev:9"]}
  },
  "dedup_clusters": [
    {"cluster_id": "cl:1", "member_indices": [0, 3],
     "match_rule": "doi_exact",
     "entries": [{"member_index": 0, "db": "crossref",
                  "source_file": "...", "locator": "...", "title": "..."}]}
  ],
  "conflicts": [
    {"conflict_id": "cf:1", "record_id": "rec:1", "field": "year",
     "values": [{"value": 2019, "db": "crossref", "locator": "..."}],
     "resolution": "priority", "resolved_value": 2020,
     "rule": "field_priority: year", "needs_human_arbitration": true}
  ],
  "coverage_evidence": [
    {"db": "crossref", "field": "abstract", "absent": true,
     "records_missing": 7, "record_locators": ["...#/message/items/0"]}
  ],
  "reference_links": [
    {"source_file": "paper.pdf", "line": 12,
     "identifier": "10.x/...", "record_ids": ["rec:3"]}
  ]
}
```

- `claims`：一条合并记录的一个字段值 = 一条结论，指向支撑它的 `evidence_id`。
- `field_sources`：`record_id → field → evidence_id[]`，说明每个字段由哪些证据贡献。
- `dedup_clusters`：每个去重簇的成员、规则与成员来源；证据见同编号 `dedup_match` 证据。
- `conflicts`：字段级冲突，列出所有取值与消解结果；`needs_human_arbitration` 指明是否需人工。
- `coverage_evidence`：**缺席即证据**——已定位到记录容器、但该字段键不存在，给出缺失记录数与 locator。
- `reference_links`：PDF/笔记中引用的 DOI/PMID 与候选集的交叉链接（参考图）。

## 4. 证据记录（evidence_records[]）类型

| type | 含义 | 关键字段 |
|---|---|---|
| `field_value` | 字段取值 | `locator` + `quote` + `pointer` + `key_path` + `value` |
| `dedup_match` | 去重判定 | `rule` + `quote_left/right` + `locator_left/right` |
| `conflict` | 冲突值 | 各取值 + locator（冗余于 conflicts 面） |
| `coverage_gap` | 缺失字段 | `absent` + `record_locators` |
| `document_mention` | 文档引用 | `identifier` + 行 locator + `quote`（整行） |
| `query` | 查询式 | `quote` = 响应自带的查询串 |

## 5. 回源核对方法（自检脚本已实现）

```python
obj = json.load(open(source_file, encoding="utf-8-sig"))
record = navigate_pointer(obj, pointer)      # 定位记录容器
raw_val = navigate_key_path(record, key_path)  # 定位字段
assert render_quote(raw_val) == quote          # 逐字相等
```

`selftest.py` 对每条 `field_value` 证据执行上述核对；`--repro` 另做同输入两次运行的确定性比对（去除墙钟时间戳后一致）。

## 6. 诚实性边界

- 不虚构 DOI/PMID/引文/年份/命中数/评测分数。
- 缺失填 `null`（缺失）与 `not_reported` 语义区分；本包统一以 `null` + `missing_metadata` 表达，不写占位字符串冒充值。
- 证据只能指回 `/input` 下实际存在的文件；源文件不可用时自检跳过核对而非判定伪造。
- `quote` 只做无损序列化，不截断、不翻译、不合并多值（多值原样保留为数组）。
