# evidence_map 结论级证据契约（溯源强化版）

本文件定义本 Skill 的核心数据结构：**结论（claim）** 与 **证据（evidence）** 的逐条回链方式。目标是让 `review.md` 里每一个 `[S#]` 都能被独立复核到原文。

## 1. 两级结构

| 层级 | 文件 | 粒度 | 作用 |
|---|---|---|---|
| 证据行 | `evidence_table.jsonl` | 一篇文献的一条可比较事实 | 从原文抽取，逐字引用 |
| 结论映射 | `evidence_map.json` | 综述里的一个论断 `[S#]` | 把多条证据聚合成结论，标注综合逻辑 |

错误定位规则：quote 与原文不符 → 证据抽取错；claim 的 evidence_ids 选错 → 归纳错；`[S#]` 在 evidence_map 缺项 → 草稿引用错。

## 2. evidence_table.jsonl（每行一个 JSON 对象）

字段（全部必填，无信息填 `null`）：

```json
{
  "evidence_id": "E01",
  "source_id": "src03",
  "locator": "materials/study_a.pdf#p3:para2",
  "quote": "逐字原文，不做任何改写或省略号外拼接",
  "claim": "该证据支撑的短论断",
  "strength": "established|suggestive|disputed|gap",
  "field": "population|method|sample|result|conclusion|limitation|not_reported",
  "notes": "抽样/对照组/统计量等可比较上下文"
}
```

- `locator` 规范：`<文件相对路径>#p<页码>:<段落>` 或 `#sec:<节标题>`；题录用 `#entry:<键>`；摘要用 `#abstract`。页码取 PDF 物理页（pdftotext 的 `\f` 分页），段落取抽取文本内的段落序号。
- `quote` 必须逐字复制；若原文长，截取完整句子并注明截断，不得改写语序或替换词。
- `strength` 与 `field` 见下。

## 3. evidence_map.json（结论级）

```json
{
  "version": 1,
  "case_id": "…",
  "claims": [
    {
      "claim_id": "S1",
      "text": "综述里带 [S1] 的完整论断",
      "claim_type": "fact|inference|disputed|gap",
      "strength": "established|suggestive|disputed|gap",
      "evidence_ids": ["E01", "E02"],
      "quotes": [
        {"evidence_id": "E01", "source_id": "src03",
         "locator": "materials/study_a.pdf#p3:para2",
         "quote": "逐字原文"}
      ],
      "note": "综合逻辑：多源一致 / 冲突并列 / 空白依据 / 推断边界"
    }
  ]
}
```

- `claim_id` 与 `review.md` 的 `[S#]` 一一对应；`validate_result.py` 会强制校验双向一致。
- `claim_type`：
  - `fact`：单源或多源直接陈述（可逐字回链）；
  - `inference`：跨源合理推断，必须在 `note` 写明推断步骤与边界；
  - `disputed`：结论相反或方法互斥，并列呈现且不得只留一方；
  - `gap`：基于"哪些问题无文献覆盖"的缺失性判断。
- 每条 claim 至少一条 `quote`，`locator` 与 `quote` 均须非空。

## 4. strength 与措辞映射

| strength | 判定 | 正文措辞 |
|---|---|---|
| `established` | 多源一致且方法充分 | 表明 / 证实 |
| `suggestive` | 单源或方法有限或仅摘要 | 提示 / 初步表明 |
| `disputed` | 存在冲突证据 | 存在争议 / 结论不一 |
| `gap` | 无文献覆盖 | 未见报告 / 缺乏证据 |

"未见报告"是缺失性判断，不等于"不存在"；两者必须区分表述。

## 5. 溯源检查（validate_result.py 强制执行）

1. `review.md` 中每个 `\[S(\d+)\]` 在 evidence_map 有同名 claim_id；
2. evidence_map 中每条 claim 的 quotes 非空，且每条 `locator` 与 `quote` 均非空；
3. 每条 claim 的 `evidence_ids` 都能在 evidence_table 中找到；
4. evidence_table 每行含全部必填字段，`quote` 非空；
5. 未被正文引用的 claim 允许存在，但会列入告警（提示可能未用到或漏引用）。

## 6. 端到端示意（合成示例，非正式答案）

下面用一个两人造文献的小例子说明四个产物如何互锁。所有内容均为示意，运行时不得套用。

**screening_log.json**

```json
{
  "criteria": {
    "population": "暴露于 X 的成年人群",
    "include": "报告 X 与 Y 结局关联的原始研究",
    "exclude": "病例报告、仅机制研究、无结局数据",
    "dedup": "按 DOI 优先，其次题名归一化，最后内容 SHA-256"
  },
  "decisions": [
    {"source_id": "src01", "file": "materials/a.pdf", "decision": "include",
     "reason": "报告 X→Y 的队列结果"},
    {"source_id": "src02", "file": "materials/b.pdf", "decision": "include",
     "reason": "报告 X→Y 的横断面结果"},
    {"source_id": "src03", "file": "materials/c.pdf", "decision": "abstract_only",
     "reason": "仅有摘要，无全文"}
  ],
  "duplicates": [
    {"keep": "src01", "dropped": "src04", "basis": "doi"}
  ]
}
```

**evidence_table.jsonl（两条）**

```json
{"evidence_id":"E01","source_id":"src01","locator":"materials/a.pdf#p4:para2","quote":"X exposure was associated with higher Y (OR 1.4, 95% CI 1.1-1.8).","claim":"X 与 Y 升高相关","strength":"suggestive","field":"result","notes":"n=1200，队列"}
{"evidence_id":"E02","source_id":"src02","locator":"materials/b.pdf#p6:para1","quote":"No association between X and Y was found (OR 0.98, 95% CI 0.80-1.20).","claim":"X 与 Y 无关联","strength":"suggestive","field":"result","notes":"n=500，横断面"}
```

**evidence_map.json（三个结论：事实 / 争议 / 空白）**

```json
{"version":1,"case_id":"demo","claims":[
  {"claim_id":"S1","text":"两项研究均直接测量了 X 与 Y 的关联。",
   "claim_type":"fact","strength":"established","evidence_ids":["E01","E02"],
   "quotes":[
     {"evidence_id":"E01","source_id":"src01","locator":"materials/a.pdf#p4:para2","quote":"X exposure was associated with higher Y (OR 1.4, 95% CI 1.1-1.8)."},
     {"evidence_id":"E02","source_id":"src02","locator":"materials/b.pdf#p6:para1","quote":"No association between X and Y was found (OR 0.98, 95% CI 0.80-1.20)."}],
   "note":"两源都报告了 X→Y 的效应估计，方向相反"},
  {"claim_id":"S2","text":"X 与 Y 的关联方向不一致。",
   "claim_type":"disputed","strength":"disputed","evidence_ids":["E01","E02"],
   "quotes":[
     {"evidence_id":"E01","source_id":"src01","locator":"materials/a.pdf#p4:para2","quote":"X exposure was associated with higher Y (OR 1.4, 95% CI 1.1-1.8)."},
     {"evidence_id":"E02","source_id":"src02","locator":"materials/b.pdf#p6:para1","quote":"No association between X and Y was found (OR 0.98, 95% CI 0.80-1.20)."}],
   "note":"队列提示正关联，横断面未见关联；结论冲突并列，不择一"},
  {"claim_id":"S3","text":"X 与 Y 的剂量-反应关系未见报告。",
   "claim_type":"gap","strength":"gap","evidence_ids":[],
   "quotes":[
     {"evidence_id":"E01","source_id":"src01","locator":"materials/a.pdf#abstract","quote":"（摘要未报告剂量-反应分析）"},
     {"evidence_id":"E02","source_id":"src02","locator":"materials/b.pdf#abstract","quote":"（摘要未报告剂量-反应分析）"}],
   "note":"两篇均未覆盖剂量-反应，属缺失性判断而非'不存在'"}
]}
```

**review.md 片段**

> 两项研究均直接测量了 X 与 Y 的关联 [S1]。队列研究提示正关联，横断面研究未见关联，X 与 Y 的关联方向不一致 [S2]。X 与 Y 的剂量-反应关系未见报告 [S3]。

**conflicts_and_gaps.md 片段**

> - 冲突：src01 报告 OR 1.4（正关联），src02 报告 OR 0.98（无关联）；设计（队列 vs 横断面）与样本量不同，未能归因。
> - 空白：剂量-反应、亚组、长期结局均无文献覆盖（基于 S3 的缺失性判断）。

## 7. 常见溯源错误与自纠（写作时对照）

| 错误模式 | 后果 | 自纠 |
|---|---|---|
| quote 用改写/意译替代逐字原文 | 无法核对"引述强度与原文一致" | 回到原文复制完整句子，仅截断不换词 |
| 把摘要当全文证据却标 `established` | 引述强度越界 | 标 `abstract_only`，strength 降为 `suggestive`/`gap` |
| 一个 `[S#]` 缺 quotes 或 locator 为空 | validate 硬错误 | 每条 claim 至少一条 locator+quote 非空 |
| 冲突只留一方 | 抹平反面证据 | 双方并列，strength=`disputed`，写入 conflicts_and_gaps |
| 空白写成"还需更多研究"套话 | 空白无据 | 落到具体证据缺口：哪一结局/人群/方法无任何文献覆盖 |
| 跨文档串值（A 的数值填进 B） | 虚假引用 | 数值只取自其 locator 指向的那篇文档 |
| 把相关写成因果 | 越界推断 | 相关用"关联"；因果仅在原文声称因果时保留 |
| locator 用文件名猜页码 | 无法定位 | 页码取 PDF 物理页（`\f` 分页），段落取抽取文本序号 |


