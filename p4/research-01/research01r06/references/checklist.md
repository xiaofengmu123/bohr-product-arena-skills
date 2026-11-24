# 六点覆盖与异常处理检查清单

对照题目六类要点与“可靠体验”评分维度，交付前逐条核验。`main.py selfcheck` 会对其中可机器检查的项目产出 `coverage` 报告，其余项目由人工/AI 复核。

## 六类要点

| # | 要点 | 检查对象 | 通过标准 | 自检方式 |
|---|---|---|---|---|
| 1 | 问题拆解 | `plan.json.subquestions` | ≥2 个，各带 id/text/depends_on/search | `selfcheck.coverage.decomposition` |
| 2 | 检索覆盖 | `search_order` + `stop_conditions` | 非空，无重复查同一批文献 | `selfcheck.coverage.search_plan` |
| 3 | 证据回链 | `evidence_matrix` + `conclusions.evidence_ids` | 每行有 locator+quote；结论证据 id 全部命中 | `selfcheck.coverage.evidence_linkage` |
| 4 | 冲突证据 | `uncertainty.conflicts` | 每条并列 ≥2 来源 + resolution | `selfcheck.coverage.conflicts` |
| 5 | 综合结论 | `research_product` | conclusions 非空、summary 非空、与问题/证据一致 | `selfcheck.coverage.conclusions` |
| 6 | 研究空白 | `uncertainty.gaps` / `pending_human` | 具体写出缺什么，不用套话 | `selfcheck.coverage.gaps` |

## 可靠体验

| 场景 | 处置 | 验证 |
|---|---|---|
| 空输入目录 | 退出码 0，`status=insufficient_evidence`，写合法 result.json | `selftest.py` S2 |
| 坏 JSON / 坏编码 | 捕获记入 `errors`/`degradations`，继续处理其余材料 | `selftest.py` S3/S4 |
| 缺参数/未知参数 | `--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work` | `main.py --help` 退出码 0 |
| PDF 无文本层 | `judgment=extraction_failed`，建议 OCR/人工，不硬读像素 | 人工核验 figure_model |
| 工具报错/限流 | 记入 `tool_calls.jsonl` 与 `errors`，重试一次后降级 | 人工核验 tool_calls |
| 证据冲突 | 并列 + `undetermined`，不静默取一方 | `selfcheck.coverage.conflicts` |
| 重复运行 | 固定证据 id、确定性布局，同输入同输出 | 两次运行 diff |
| 中途异常 | 保留 partial 产物，退出码 0 | 人工核验 change_log |

## 提交前自查命令

```bash
python3 -B scripts/main.py --help
python3 -B scripts/selftest.py
python3 -B scripts/main.py selfcheck --input /input --output /app/output --work /app/work
```

三个命令都应退出码 0；`selfcheck` 输出 `result_parseable: true` 且 `valid: true`；六项 `coverage` 中可检查项应为 `ok`（冲突为空时 `ok:null`，属正常，但需在 `gaps` 中说明“未发现冲突”的依据）。
