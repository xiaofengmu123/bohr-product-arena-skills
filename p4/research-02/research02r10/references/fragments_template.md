# IR 片段最小示例（模板，非正式结果）

以下片段展示 `/app/work` 下的**结构形状**；内容为占位示例，运行时应替换为真实文献中的逐字材料。所有示例在 `/app/output` 交付前必须由真实证据替换，禁止直接提交。

## screening.json

```json
{
  "criteria": {
    "research_object": "研究对象/人群",
    "inclusion": ["纳入条件1", "纳入条件2"],
    "exclusion": ["排除条件1"],
    "dedup_rule": "按 SHA-256 / DOI / 题名去重"
  },
  "decisions": [
    {"source_id": "S01", "decision": "include", "reason": "符合纳入条件且报告目标结局", "locator": "paper_a.pdf#p1"},
    {"source_id": "S02", "decision": "abstract_only", "reason": "仅有摘要，无全文", "locator": "record.json"}
  ]
}
```

## evidence.jsonl（每行一条）

```json
{"evidence_id": "E01", "source_id": "S01", "locator": "paper_a.pdf#p3", "quote": "逐字原文摘录……", "claim": "该证据支撑的论断", "strength": "suggestive", "field": "result", "notes": ""}
```

## themes.json

```json
{
  "themes": [
    {
      "theme_id": "T1",
      "title": "主题标题",
      "summary": "从证据聚类得出的概括",
      "supporting_sources": ["S01", "S03"],
      "subthemes": [{"title": "子主题", "sources": ["S01"]}],
      "boundary": "该主题不覆盖的内容"
    }
  ]
}
```

## claims.json

```json
{
  "paragraphs": [
    {
      "heading": "结果",
      "claims": [
        {"claim_id": "C1", "text": "一句带强度的论断（措辞按 strength）", "evidence_ids": ["E01"], "strength": "suggestive"}
      ]
    }
  ]
}
```

## conflicts_gaps.json

```json
{
  "conflicts": [
    {
      "conflict_id": "X1",
      "description": "两源结论相反的描述",
      "sides": [
        {"claim": "一方结论", "evidence_ids": ["E02"]},
        {"claim": "另一方结论", "evidence_ids": ["E05"]}
      ],
      "status": "disputed"
    }
  ],
  "gaps": [
    {"gap_id": "G1", "description": "没有任何文献覆盖的问题", "basis": "inventory 中 5 篇均未报告该结局"}
  ]
}
```

## meta.json（scaffold 已写，Agent 只改 status 与追加 change_log）

```json
{
  "status": "ok",
  "uncertainty": ["仅单来源支持的结论"],
  "pending_human": ["需人工确认的元数据"],
  "degradations": [],
  "errors": [],
  "change_log": [{"ts": "2026-11-22T00:00:00Z", "event": "写入筛选决定"}]
}
```
