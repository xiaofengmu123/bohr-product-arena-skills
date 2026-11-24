# 六点覆盖与健壮性检查清单

交付前逐条核对。脚本侧等价检查：`python3 -B scripts/main.py selfcheck`（契约校验 + 六点覆盖），本地回归：`python3 -B scripts/selftest.py`。

## A. 六点覆盖（对应"实际效果/完整性"）

- [ ] 问题拆解：`decomposition.md` 含 ≥2 个 `SQ1..SQn` 子问题，每个子问题可独立检索，并标注依赖边（前置/可并行）。
- [ ] 检索计划：`search_plan.md` 写清顺序、来源范围、停止条件（饱和/去重/预算耗尽），无重复查同一批文献。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_matrix`；每行有 `locator + quote`，`quote` 逐字取自材料。
- [ ] 冲突证据：`uncertainty.conflicts` 每条并列两个来源 + 解决说明；未静默择一；无法裁定标 `undetermined`。
- [ ] 综合结论：`research_product.conclusions` 非空，结论与研究问题、证据范围一致。
- [ ] 研究空白：`uncertainty.gaps` / `pending_human` 显式写"缺什么"，基于检索缺口而非套话。

## B. 契约完整（本版侧重）

- [ ] `result.json` 含 `case_id` 与 `output_contract` 全部字段。
- [ ] `selfcheck` 报 `missing_contract_fields=[]`、`validation_issues=[]`。
- [ ] `contract_audit` 存在且字段对齐可复核。
- [ ] `change_log` 含 `skeleton`→`merge`→`finalize` 记录。

## C. 异常处理（对应"可靠体验"）

| 场景 | 预期行为 |
|---|---|
| 空输入目录 | 退出码 0，写 `status=insufficient_evidence`，不抛异常 |
| 坏 JSON / 坏编码 | 捕获并记入 `errors`/`degradations`，继续处理其余材料 |
| PDF 无文本层 | 证据行 `judgment=extraction_failed`，建议 OCR/人工，不硬读像素 |
| 检索为空 / 工具限流 | 记入 `limitations`，降级为 partial，不编造 |
| 缺软件 / 缺权限 | 记入 `limitations`/`errors`，输出最小合法产物 |
| 重复运行 | 字段顺序、证据 id、结论排序确定；时间戳/runtime 允许变化 |

## D. 交付前硬性检查

```bash
python3 -B scripts/main.py --help                 # 退出码 0
python3 -B scripts/main.py --input ./demo/input --output ./demo/output
python3 - <<'PY'
import json
r = json.load(open('./demo/output/result.json', encoding='utf-8'))
assert r.get('case_id') is not None and r.get('status') in \
    ('ok','partial','insufficient_evidence','input_error')
assert r.get('evidence_matrix') is not None
print('ok', r['status'])
PY
```

- 空目录、坏 JSON、缺输入三种情况各跑一次（`selftest.py` 已覆盖）。
- 产物：`result.json` 可解析、`report.md`/`evidence_map.json` 存在。
- 不生成正式测试用例答案、不伪造 benchmark 分数。
