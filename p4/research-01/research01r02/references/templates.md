# 可复用模板与最小调用示例

本节提供可直接套用的模板；内容全部是**结构示意**，不是任何正式案例的答案。Agent 在执行时按 `SKILL.md` 阶段把真实内容填入对应文件。

## 1. decomposition.md 模板

```markdown
# 问题拆解

研究问题：<一句话，含对象/机制/方法/时间范围/比较关系/证据要求>

## 子问题
- SQ1：<可被一次检索回答的问题>（前置=无，可并行）
- SQ2：<…>（前置=SQ1，依赖其检索式/材料）
- SQ3：<…>（前置=SQ2）

## 依赖图
SQ1 ──> SQ2 ──> SQ3
SQ1（并行）SQ4
```

合格标准：≥2 个 SQ；每个 SQ 明确要取回的字段；依赖边不缺失；不是原句换关键词。

## 2. search_plan.md 模板

```markdown
# 检索计划
顺序：先 SQ1（材料内定位），再 SQ2（依赖 SQ1 的基线），最后 SQ3。
来源范围：/input/materials/** 优先；外部检索仅用于补缺口（检索式：…）。
停止条件：每条结论有逐字定位即停；出现显式缺口即停；追加检索边际收益为 0 即停。
去重：同一检索式/同一批文献不重复执行。
```

## 3. evidence_cards.jsonl 行模板

```json
{"id":"E1","subquestion":"SQ1","source":"materials/a.pdf","locator":"p3 §2.2",
 "quote":"逐字引用（必须逐字）","claim":"支持的论断","judgment":"supports",
 "reason":"理由","conclusion_ids":["C1"],
 "trace":{"tool":"pdf_text","params":{"file":"a.pdf"},"outcome":"ok"}}
```

`judgment` 取值：`supports / partial / unsupported / conflicts / extraction_failed`。

## 4. payload.json 模板（供 merge 增量合并）

```json
{
  "research_product": {
    "type": "synthesis",
    "question": "研究问题",
    "summary": "综合结论",
    "conclusions": [
      {"id": "C1", "text": "结论句", "evidence_ids": ["E1"],
       "strength": "established"}
    ]
  },
  "uncertainty": {
    "conflicts": [
      {"sources": ["材料A", "材料B"], "claim": "冲突指标",
       "status": "undetermined", "resolution": "未裁定原因"}
    ],
    "gaps": [
      {"what": "缺什么字段/材料", "why": "为什么缺", "affects": "影响哪条结论"}
    ]
  },
  "status": "ok"
}
```

`merge` 只追加、不覆盖；需要纠正的内容在 `synthesis.md` 说明并由 `finalize` 规整。

## 5. 最小调用示例（示意，非正式案例）

```bash
# 1) 发现输入（示意输出已标注为示例）
python3 -B scripts/main.py discover
# {"exists": true, "case_json": "/input/case.json", "contract_fields": ["findings"], …}   # 示例

# 2) 前 3 分钟内落盘骨架
python3 -B scripts/main.py skeleton
# {"wrote": true, "status_hint": "partial", …}                                            # 示例

# 3) 阶段记账（P1 结束，已用 12 次请求）
python3 -B scripts/main.py checkpoint --phase P1 --requests 12
# {"remaining_seconds": 600, "remaining_requests": 52, "late": false, "p0_ok": true}       # 示例

# 4) 综合后增量合并
python3 -B scripts/main.py merge --payload /app/work/payload.json

# 5) 收尾 + 自检
python3 -B scripts/main.py finalize
python3 -B scripts/main.py selfcheck
# {"ok": true, "summary": "11/11 项通过"}                                                  # 示例
```

以上输出均为**示意**，实际值取决于运行时 `/input` 与执行进度；不冒充任何正式案例结果。
