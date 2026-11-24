# 自检清单、异常处置与最小调用示例

## 1. 自检清单（对应评分维度）

执行 `python -B scripts/main.py selfcheck` 会重跑下述检查项；`finalize` 在赋 status 之前已跑同一套门禁（校验顺序：先校验、后定 status）。

| # | 检查项 | 通过条件 | 对应评分维度 |
|---|---|---|---|
| 1 | 问题拆解 | `review_question` 非空；`subquestions` ≥1 | 实际效果·任务完成度 |
| 2 | 检索式 | 每 `query_blocks` 含 database+query+intent | 实际效果·结果正确性 |
| 3 | 纳排标准 | 纳/排条款均 ≥1 且每条含 id+clause | 实际效果·结果正确性 |
| 4 | 筛选记录 | 两轮记录存在；每 exclude 含 reason+criterion_id | 实际效果·可核验性 |
| 5 | 质量评价 | 每纳入研究含维度、总体等级、basis_locator+quote | 实际效果·正确性 |
| 6 | 证据回链 | 每证据卡含 locator+quote；结论 evidence_ids 命中证据表 | 实际效果·可核验性 |
| 7 | 冲突与空白 | 冲突逐条 ≥2 来源；空白落位到具体缺口 | 实际效果·完整性 |
| 8 | 综合结论 | 结论非空且与研究问题、证据范围一致 | 实际效果·完整性 |
| 9 | 完整合法 | result.json 可 `json.load`；output_contract 字段齐全 | 可靠体验·运行稳定 |
| 10 | 异常处理 | 空输入/坏 JSON/坏编码不崩溃，退出码 0 | 可靠体验·异常处理 |
| 11 | 可复用 | 检索式/纳排模板/质量量表/筛选表见 `references/protocol.md` | 生态贡献·可复用性 |

## 2. 异常处置（全部退出码 0，除非 selfcheck 加 --strict）

| 场景 | 处置 |
|---|---|
| 空输入目录 | `status=insufficient_evidence`，`pending_human` 列缺口 |
| 坏 JSON | 记 `errors`，继续处理其余材料 |
| 坏编码 | `utf-8-sig → gb18030 → latin-1` 降级，记 `degradations` |
| 检索为空/数据库超时 | 检索块标 `not_run`，记 `degradations`，不伪造 hits |
| 全文不可得 | 该篇标 `abstract_only`，结论降为 suggestive/not_reported |
| 术语歧义（同一概念多种表述） | 检索式显式列出同义词/受控词并写 intent |
| 纳排条款冲突 | 按 priority 低值优先；同级冲突标 undetermined |
| 纳入证据互相矛盾 | `conflicts` 并列两来源，标 undetermined |
| 权限不足 | 记 `limitations`/`errors`，降级为最小合法产物 |

## 3. 最小调用示例（示意输出，非正式案例）

```bash
python -B scripts/main.py discover
# {"input_dir": "/input", "exists": true, "case_json": "/input/case.json", ...}

python -B scripts/main.py skeleton
# {"case_id": "case-xxx", "contract_fields": [...], "wrote": true, ...}

python -B scripts/main.py record --kind screening --json \
  '{"round":"title_abstract","source_id":"S1","decision":"exclude","reason":"case report","criterion_id":"E3","judge":"agent"}'
# {"ok": true, "kind": "screening", "errors": []}

python -B scripts/main.py finalize
# {"ok": true, "status": "partial", "checks": [...], "errors": []}

python -B scripts/main.py selfcheck --strict
# {"ok": false, "status": "partial", "missing_contract": [], "checks": [...]}
```

示意输出仅说明接口与流程，不冒充正式案例结果。
