# 规范工作文件 Schema 与可复用模板

本文档定义 `/app/work/` 下规范工作文件（中间表示）的字段约定，以及可被他人复用的检索式模板、纳排条款模板与质量评价量表。`scripts/main.py` 按此 schema 做写时校验与重放。

## 1. 中间表示：六个工作文件

`result.json` 不手工编辑；它是下列文件的确定性重放结果。

| 文件 | 类型 | 内容 |
|---|---|---|
| `protocol.json` | 对象 | 研究问题、子问题、检索式、纳排标准（先于筛选落盘） |
| `sources.json` | 数组 | 识别到的题录（identified set） |
| `screening.jsonl` | 逐行对象 | 题录/全文两轮筛选决定 |
| `quality.jsonl` | 逐行对象 | 纳入研究的逐维度质量评价 |
| `evidence.jsonl` | 逐行对象 | 证据卡（locator + 逐字 quote） |
| `synthesis.json` | 对象 | 综合结论、冲突证据、研究空白 |

## 2. protocol.json

```json
{
  "review_question": "……（PICO/要素化表述）",
  "subquestions": [
    {"id": "sq1", "question": "……", "depends_on": []}
  ],
  "search_strategy": {
    "query_blocks": [
      {
        "id": "qb1",
        "database": "PubMed|Web of Science|Scopus|CNKI|……",
        "fields": ["title", "abstract", "keywords"],
        "query": "(…… AND …… OR ……)",
        "intent": "该段检索式覆盖哪一个研究要素",
        "date_range": {"from": "2015-01-01", "to": "2025-12-31"},
        "language": ["en", "zh"],
        "status": "run | not_run",
        "hits": null
      }
    ]
  },
  "inclusion_criteria": [
    {"id": "I1", "clause": "研究类型/对象/时间/语言/可得全文……", "priority": 1}
  ],
  "exclusion_criteria": [
    {"id": "E1", "clause": "……", "priority": 1}
  ],
  "criterion_conflict_policy": "排除优先于纳入；同级冲突记录并标 undetermined",
  "quality_scale": "quality_scale_v1"
}
```

**检索式模板**（把研究问题转成可执行检索式，禁止退化成关键词串）：
- 要素分解：对象/人群、干预或暴露、对照、结局、研究类型、时间、语言。
- 每个 `query_blocks` 必须同时给出 `database + fields + query + intent + date_range + language`。
- `query` 用布尔逻辑与括号显式组合同义词与受控词；`intent` 说明该段覆盖哪个要素。

**纳排条款模板**（条款化、可判定、含优先级）：
- 每条含 `id + clause + priority`；clause 写成“是/否”可判定命题。
- 排除条款默认优先于纳入条款；同级冲突按 priority 低值优先，仍冲突则 `undetermined`。
- 条款必须在任何筛选决定之前写入 `protocol.json`，筛选途中不得悄悄放宽。

## 3. screening.jsonl（每行一条，两轮）

```json
{"round": "title_abstract", "source_id": "S3", "decision": "exclude",
 "reason": "……", "criterion_id": "E2", "judge": "agent", "duplicate_of": null}
{"round": "full_text", "source_id": "S3", "decision": "include",
 "reason": null, "criterion_id": "I1", "judge": "agent"}
```

- `round` 取值 `title_abstract | full_text`；`decision` 取值 `include | exclude | unclear`。
- **硬规则**：`exclude` 必须同时给 `reason + criterion_id`；去重用 `duplicate_of` 指向保留记录。
- 每个 source 至少一条 `title_abstract`；判定依据是材料本身，不依赖文件名标签。

## 4. quality.jsonl（每行一个纳入研究）

```json
{"source_id": "S3", "scale": "quality_scale_v1", "overall": "high",
 "impact": "支持结论 C1，权重高",
 "dimensions": [
   {"dim": "selection", "score": "low", "basis_locator": "materials/x.pdf#p3",
    "quote": "……逐字引用……"},
   {"dim": "comparability", "score": "high", "basis_locator": "materials/x.pdf#p5",
    "quote": "……"}
 ]}
```

- `score` 取值 `low | unclear | high`；每维度必须给 `basis_locator + quote`（逐字）。
- `overall` 取值 `high | moderate | low | unclear`，并写 `impact`（该等级如何影响后续结论）。

**可复用质量量表（quality_scale_v1，适用于纳入研究的通用内部效度评价）**：

| 维度 | 判定问题 |
|---|---|
| selection | 样本/研究选择是否明确、可重复，偏倚风险如何 |
| comparability | 是否设对照或可比组、混杂是否处理 |
| measurement | 结局/暴露测量是否可靠、是否盲法或客观 |
| completeness | 随访/数据完整性、缺失是否说明 |
| reporting | 方法、结果、限制是否充分报告 |

## 5. evidence.jsonl（证据卡）

```json
{"evidence_id": "EV07", "source_id": "S3",
 "claim": "……本证据支持什么断言……",
 "locator": "materials/x.pdf#p3", "quote": "……逐字取自材料……",
 "kind": "fact | inference | not_reported", "strength": "firm | suggestive"}
```

- `quote` 必须逐字取自材料；`locator` 用 `文件名#页码/段落` 定位。
- `kind` 区分材料事实、合理推断、未报告；`strength` 标注证据强度。

## 6. synthesis.json

```json
{
  "conclusions": [
    {"id": "C1", "statement": "……", "evidence_ids": ["EV07", "EV11"],
     "confidence": "high | moderate | low"}
  ],
  "conflicts": [
    {"id": "F1", "claim": "……",
     "sources": [
       {"source_id": "S2", "evidence_id": "EV02", "position": "……"},
       {"source_id": "S5", "evidence_id": "EV09", "position": "……"}
     ],
     "resolution": "undetermined | resolved"}
  ],
  "gaps": [
    {"id": "G1", "kind": "evidence_gap | design_gap | coverage_gap",
     "description": "……具体缺口……"}
  ],
  "biases": []
}
```

- 每条结论的 `evidence_ids` 必须命中证据卡 id；冲突至少并列两个来源；空白落位到具体缺口。
