# 证据表与引用规范

本文档是可执行规范：`evidence_table.jsonl` 每行、`review.md` 每条引用、`screening_log.json` 每条决定都按此落地，保证产物可逐条复核。

## 1. 筛选记录（screening_log.json）

结构（对象，可带多个数组）：

```json
{
  "criteria": {
    "population": "研究对象（如某疾病人群/某材料体系）",
    "inclusion": ["纳入条件"],
    "exclusion": ["排除条件"],
    "dedup": "去重规则：DOI 优先，其次题名规范化，再次 SHA-256"
  },
  "decisions": [
    {
      "source_id": "S001",
      "title": "…",
      "decision": "include | exclude | duplicate",
      "reason": "…",
      "evidence_locator": "screening 依据所在文件+段落"
    }
  ],
  "duplicates": []
}
```

硬要求：`criteria` 必须写在 `decisions` 之前；每条决定给 `reason` 与 `evidence_locator`，不留无法解释的纳排。

## 2. 证据行字段（evidence_table.jsonl）

每行一个 JSON 对象，字段：

| 字段 | 必需 | 含义 |
|---|---|---|
| `evidence_id` | 是 | 唯一行号，`E001` 起，与正文 `[S#]` 一一对应 |
| `source_id` | 是 | 指向 sources 表中的来源 |
| `locator` | 是 | 文件 + 页码/段落，如 `materials/paper.pdf:3` 或 `extracted/paper.pdf.txt#p2` |
| `quote` | 是 | 逐字原文摘录（可截断，加 `…`），用于核验论断是否被原文支持 |
| `claim` | 是 | 该证据支撑的中文论断（一句话） |
| `strength` | 是 | `established / suggestive / disputed / not_reported` |
| `field` | 是 | 比较字段：population / method / sample / result / conclusion / limitation / not_reported |
| `notes` | 否 | 补充：样本量、效应量、方法局限等 |

`quote` 必须逐字取自原文；不得改写、不得拼接不同文档的片段。

## 3. strength 与措辞对应

| strength | 判定 | 正文措辞 |
|---|---|---|
| `established` | 多源一致且方法充分 | 表明 / 证实 |
| `suggestive` | 单源或方法有限、仅摘要 | 提示 / 初步表明 |
| `disputed` | 至少两源结论相反 | 存在争议 / 结论不一致 |
| `not_reported` | 材料未报告该指标 | 未见报告 |

"未见报告"只表示本批材料没有该数据，不推论"不存在"。

## 4. 冲突与空白（conflicts_and_gaps.md）

- 冲突：列出结论相反或方法互斥的来源对，各自给 `source_id` + `locator` + `quote`，并列呈现，不强行调和。
- 空白：每条空白必须落到具体证据缺口——"哪些问题在本批文献中没有任何证据行覆盖"，并标注 `not_reported`；禁止用"未来需更多研究"这类套话收尾。

## 5. sources 表

`result.json` 的 `sources` 为数组，每项含 `source_id`、`title`、`authors`、`year`、`doi`（仅当原文/题录确有）、`path`、`type`（`fulltext / abstract_only / record`）、`notes`。缺元数据填 `null` 或 `not_reported`，不编造 DOI 或年份。

## 6. 禁止事项

- 虚构文献、DOI、数值、页码；
- 跨文档串值：A 文的样本量/效应量不可填进 B 文的证据行；
- 把相关写成因果：无因果设计的证据只能用"相关/关联"；
- 把摘要当全文：`abstract_only` 来源只支撑摘要层面论断，正文数据标 `not_reported`。
