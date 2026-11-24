# 系统性文献综述执行协议

本文件定义五个环节的工作文件结构、字段含义与自检标准。Agent 把实质内容写入 `/app/work/`，由 `scripts/main.py finalize` 确定性组装进 `/app/output/result.json`，避免手工改 JSON 出错。

## 1. 五环节与工作文件

| 环节 | 工作文件 | 关键字段 |
|---|---|---|
| P1 问题与协议 | `protocol.json` | `review_question`、`subquestions`、`search_strategy`、`inclusion_criteria`、`exclusion_criteria` |
| P2 两轮筛选 | `screening.jsonl` | `id`、`stage`、`decision`、`reason`、`criterion_id`、`judge`、`duplicate_of` |
| P3 质量与证据 | `quality_appraisal.jsonl`、`evidence_cards.jsonl` | `basis_locator`、`quote`、`rating`、`impact` / `evidence_id`、`locator`、`quote` |
| P4 综合 | `synthesis.json` | `conclusions`、`conflicts`、`gaps` |

## 2. 研究问题与检索式

`review_question` 必须是一条可回答的研究问题；`subquestions` 拆成 ≥2 条带依赖的子问题。`search_strategy.query_blocks` 每条至少含：

```json
{"database": "PubMed", "query": "…", "intent": "…", "time_range": "…", "language_limits": ["en"]}
```

判定标准：检索式能直接粘贴到该数据库执行，包含布尔逻辑、同义词/受控词、时间与语言限制，且 `intent` 说明该段要命中什么；**不退化成一个关键词串**。

## 3. 纳排标准（先于筛选落盘）

`inclusion_criteria` / `exclusion_criteria` 为条款化数组，每条含 `id`、`text`、`priority`（可选）。要求：

- 可判定：能仅凭题录/全文对一篇文献给出 include/exclude；
- 有优先级：条款冲突时按 `priority` 小者优先；
- 时间戳：必须早于任何筛选决定写入（记录在 `protocol.json` 内，不随筛选放宽）。

筛选时每篇 `exclude` 必须写 `reason`（逐字依据）+ `criterion_id`（命中哪条标准）+ `judge`。

## 4. 筛选记录与 PRISMA

`screening.jsonl` 每行一条决定，覆盖题录级（`stage="title_abstract"`）与全文级（`stage="full_text"`）两轮。字段：

```json
{"id": "…", "stage": "title_abstract", "decision": "include|exclude|undetermined",
 "reason": "…", "criterion_id": "…", "judge": "agent", "duplicate_of": null}
```

`finalize` 用 `recompute_prisma` 从 `screening_records` 重算 `prisma_flow` 各计数（identified、deduplicated、screened_title_abstract、excluded_title_abstract、screened_fulltext、excluded_fulltext、included）。`selfcheck` 会对比声明的 `prisma_flow` 与重算值，不一致列入 `prisma_mismatch`。

## 5. 质量评价（可解释量表）

`quality_appraisal.jsonl` 每行一条维度判断：

```json
{"id": "…", "dimension": "随机/分配隐藏/盲法/完整性/报告",
 "rating": "low risk|some concerns|high risk|unclear",
 "basis_locator": "paper.md#p3", "quote": "逐字片段", "impact": "对结论的影响"}
```

总体等级与“该等级如何影响后续结论”单独写 `overall` 一条，**不写“质量较高”这类无依据短语**。

## 6. 证据回链（硬要求）

`evidence_cards.jsonl` 每行一张证据卡：

```json
{"evidence_id": "e1", "locator": "paper.md#p3", "quote": "逐字取自材料"}
```

`quote` 必须逐字来自材料；`synthesis.conclusions/conflicts/gaps` 的每条用 `evidence_ids` 回链到证据卡。`selfcheck` 校验：

- 每条结论的 `evidence_ids` 都能在 `evidence_map` 中命中；
- 每张证据卡 `locator` 与 `quote` 均非空；
- `conflicts` 逐条并列 ≥2 个来源；`gaps` 落位到具体缺口（人群/时间/方法/数据）。

## 7. 自检清单（对应评分维度）

- [ ] 问题拆解：`review_question` 非空，`subquestions` ≥2 且带依赖；
- [ ] 检索式：每个 `query_blocks` 含 database+query+intent+时间/语言限制；
- [ ] 纳排标准：条款化、可判定、含优先级，且先于筛选落盘；
- [ ] 筛选记录：两轮逐条 decision，每条 exclude 有 `reason+criterion_id`，计数可重算；
- [ ] 质量评价：逐维度 `basis_locator+quote` + 总体等级 + 对结论影响；
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_map`，每行有 `locator+quote` 逐字；
- [ ] 冲突与空白：`conflicts` 并列两来源，`gaps` 落位到具体缺口；
- [ ] 综合结论：`conclusions` 非空且与研究问题、证据范围一致，不越界；
- [ ] 完整合法：`python -B scripts/main.py selfcheck` 通过，result.json 可 `json.load`；
- [ ] 异常处理：空输入/坏 JSON/坏编码/检索为空/全文不可得/条款冲突/证据矛盾均记录且退出码 0；
- [ ] 可复用：检索式模板、纳排模板、筛选表、质量量表可被他人复用。
