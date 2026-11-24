# 工作产物模板（可复用）

以下模板是模型在执行时应写入 `/app/work/` 的中间产物结构；脚本 `scripts/main.py` 只做确定性组装，不替模型编造内容。所有字段非空必填，材料没有的写 `pending_human` 或留缺口。

## 1. decomposition.md（问题拆解）

```markdown
# 子问题拆解

研究对象 / 机制 / 方法 / 时间范围 / 比较关系 / 证据要求：

- SQ1: <可独立检索的子问题>（前置，无依赖）
- SQ2: <子问题>（依赖 SQ1）
- SQ3: <子问题>（依赖 SQ1、SQ2）
```

判据：`SQ\d+` ≥2 个，且出现"依赖/→/前置"标注依赖边；子问题必须能映射到具体检索动作，而非原句改写。

## 2. search_plan.md（检索计划）

```markdown
# 检索计划

顺序：SQ1 → SQ2 → SQ3（SQ2、SQ3 可并行）
来源范围：<材料文件/允许的外部检索源，逐一列明>
停止条件：<证据饱和 / 无新增来源 / 预算触顶>
去重规则：<同源同检索式不重复查；不同子问题复用同一文献时只提取一次>
```

判据：含"顺序""停止""来源"三要素；明确前置检索与可并行项；写清去重规则以减少无效检索。

## 3. evidence_cards.jsonl（逐条证据卡片）

每行一个 JSON 对象，追加写：

```json
{"id":"E1","subquestion":"SQ1","source":"materials/a.pdf","locator":"p3 §2.2",
 "quote":"逐字引用（必须从材料原文复制）","claim":"该证据支持的论断",
 "judgment":"supports|partial|unsupported|conflicts|extraction_failed",
 "reason":"为何这样判定","conclusion_ids":["C1"]}
```

纪律：`quote` 必须逐字取自材料；`locator` 用页码/章节定位；`judgment=extraction_failed` 用于 PDF 无文本层等情况，并建议 OCR/人工。

## 4. payload.json（综合阶段合并负载）

```json
{
  "status": "partial|ok|insufficient_evidence",
  "research_product": {"type":"synthesis","question":"…","summary":"…",
    "conclusions":[{"id":"C1","text":"…","evidence_ids":["E1"],"strength":"established"}]},
  "evidence_matrix": [],
  "uncertainty": {"conflicts":[{"id":"X1","claim_a":"…","source_a":"…",
    "claim_b":"…","source_b":"…","resolution":"…"}],
    "biases":[],"gaps":[],"undetermined":[]},
  "sources": [{"id":"S1","path":"…","title":"…","type":"…"}],
  "limitations": [], "pending_human": []
}
```

`main.py merge --payload …` 深度合并到 result.json 并追加 change_log；`finalize` 生成 `report.md` 与 `evidence_map.json`。

## 5. trace.jsonl（工具调用留痕，使用增益可验证）

每次检索/抓取/解析/计算追加一行：

```json
{"ts":"…","step":"search","params":{"query":"…","source":"…"},
 "result_summary":"命中 N 条","retry":0,"ok":true}
```

判据：参数与返回摘要齐全、失败重试可见，使"减少无效检索轮次"可被独立复核，而不是靠文字自述。
