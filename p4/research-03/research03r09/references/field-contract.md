# 字段契约与记录格式

本文件定义 `result.json` 领域字段与 `/app/work/` 中间产物的逐字段格式。所有记录共用一条原则：**关键判断必须带 `locator`（材料内位置）与逐字 `quote`**，以便他人复跑复核。

## 1. result.json 领域字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `review_question` | string | 单一、可判定、范围明确的研究问题 |
| `subquestions` | list | 拆解后的子问题；建议每项含 `q` 与 `depends_on` |
| `search_strategy.query_blocks` | list | 检索式分块，见下 |
| `inclusion_criteria` / `exclusion_criteria` | list | 条款化标准，见下 |
| `screening_records` | list | 两轮筛选逐条记录，见下 |
| `prisma_flow` | object | 由 `finalize` 从筛选记录**重算**的计数，非手填 |
| `quality_appraisal` | list | 纳入研究的逐维度质量评价，见下 |
| `synthesis` | object | `conclusions` / `conflicts` / `gaps`，见下 |
| `evidence_map` | list | 证据卡（locator + 逐字 quote） |
| `uncertainty` | list | 冲突、偏倚、空白、无法确定项 |

### 检索块 `query_blocks[]`

```json
{"database": "PubMed", "query": "(term1[tiab] OR term2[tiab]) AND term3[MeSH]",
 "fields": "title/abstract/MeSH", "intent": "核心干预×结局",
 "date_range": "2015-01-01:2026-11-22", "language": "en",
 "hits": 128, "run": true, "note": ""}
```

- `query` 必须**可执行**（含布尔逻辑/字段限定/受控词），禁止退化为一个关键词串。
- `intent` 说明这一段检索式回答什么问题。
- 数据库不可用时 `run: false`，`hits` 留空，记 `degradations`，**不伪造命中数**。

### 纳排条款 `inclusion_criteria[]` / `exclusion_criteria[]`

```json
{"criterion_id": "I1", "rule": "随机对照或准实验设计",
 "priority": 1, "source": "protocol", "note": ""}
```

- `criterion_id` 稳定且唯一；`priority` 数字越小越优先。
- **必须先在 `protocol.json` 落盘，再开始筛选**；筛选决定只能引用已有 `criterion_id`，不得中途放宽。

## 2. `/app/work/` 中间产物

### protocol.json（P1 写，先于筛选）

```json
{"review_question": "...", "subquestions": [...],
 "search_strategy": {"query_blocks": [...]},
 "inclusion_criteria": [...], "exclusion_criteria": [...]}
```

### screening.jsonl（P2 追加，每行一个 JSON 对象）

```json
{"record_id": "S01", "source": "作者年份或文件名", "stage": "title_abstract",
 "decision": "exclude", "reason": "无干预组", "criterion_id": "E2",
 "judge": "agent", "duplicate_of": null, "locator": "records.csv#L4", "quote": "..."}
```

- `stage` ∈ `title_abstract`（题录级）/ `fulltext`（全文级）。
- `decision` ∈ `include` / `exclude`；`exclude` **必须**有 `reason` + `criterion_id`。
- 去重时把该记录填 `duplicate_of` 指向主记录 id，不计入筛选。

### quality_appraisal.jsonl（P3 写）

```json
{"record_id": "S05", "source": "...", "dimensions": [
   {"dimension": "偏倚风险", "judgment": "低", "basis_locator": "paper.pdf#p3", "quote": "..."}],
 "overall_grade": "moderate", "impact_on_conclusions": "结论按次要证据处理"}
```

- 量表五维（见 `# 3 质量评价量表`）；每个维度都要有 `basis_locator` + `quote`，不能只写“质量较高”。
- `overall_grade` ∈ `high / moderate / low / critical`；`critical` 应降级为背景或排除。

### evidence_cards.jsonl（P3 写）

```json
{"evidence_id": "EV01", "claim": "干预A显著降低结局X",
 "locator": "paperA.pdf#p4", "quote": "（逐字摘录）",
 "source": "作者年份", "strength": "direct|suggestive|not_reported"}
```

- `quote` 必须**逐字**取自材料，不得改写；`locator` 用 `文件名#页码/行号` 或段落定位。

### synthesis.json（P4 写）

```json
{"conclusions": [{"text": "...", "evidence_ids": ["EV01", "EV02"]}],
 "conflicts": [{"statement": "...", "source_a": "S02", "source_b": "S05", "resolution": "undetermined"}],
 "gaps": [{"gap": "...", "scope": "长期随访/人群/剂量"}]}
```

- 每条 `conclusion` 的 `evidence_ids` 必须命中 `evidence_cards.jsonl` 的 `evidence_id`。
- 冲突必须并列两个来源；无法裁定写 `resolution: "undetermined"`，不强行取一方。

## 3. 质量评价量表（五维）

对每篇纳入研究逐维给出 `judgment` + `basis_locator` + `quote`：

1. **研究设计**：设计是否匹配研究问题（RCT/队列/横断面/定性等）。
2. **偏倚风险**：选择/混杂/失访/检出偏倚的控制情况。
3. **测量与结局**：暴露、结局、工具的信效度与一致性。
4. **报告完整性**：方法、样本量、缺失数据、效应量与不确定性是否报告。
5. **适用性/可转移性**：对象、背景、时间范围是否可迁移到本综述问题。

总体等级：`high`（全部低偏倚，结论取全权重）/ `moderate`（部分维度有缺陷，结论带 caveat）/ `low`（主要维度有问题，结论降为 `suggestive`）/ `critical`（严重缺陷，降为背景或排除）。等级**必须**写明如何影响后续结论，否则视为未完成。
