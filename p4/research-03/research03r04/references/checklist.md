# 运行清单与失败处理

Agent 在运行每个案例时按本清单执行；交付前逐条自检。脚本与 `references/protocol.md` 的 Schema 是唯一事实来源。

## 1. 运行前（0–3 分钟内）

- [ ] `python -B scripts/main.py discover` 已列出 `case.json`、`task.md` 与全部材料；不假设文件名。
- [ ] `python -B scripts/main.py skeleton` 已成功落盘 `/app/output/result.json`（status=partial）与 `/app/work/run_state.json`。
- [ ] 已确认 `case_id` 与 `output_contract` 字段集（`skeleton` 输出里 `contract_fields` 可查）。
- [ ] 已确认预算：900 秒、64 次请求、每响应 ≤16384 token。

## 2. 运行中（增量写盘，禁止清空重建）

- [ ] PDF 文本已提取到 `/app/work/extracted/<原名>.txt`（用平台 pdf-text-tool 或 pdftotext；无文本层/低质量图标 `extraction_failed`）。
- [ ] 每个阶段结束落盘对应工作文件：`protocol.json` → `screening.jsonl` → `quality_appraisal.jsonl` → `evidence_cards.jsonl` → `synthesis.json`。
- [ ] 纳排标准在筛选决定**之前**已写入 `protocol.json`；筛选决定逐条引用 `criterion_id`。
- [ ] 每个 exclude 有 `reason + criterion_id`；每个 duplicate 有 `duplicate_of`。
- [ ] 证据卡 `quote` 逐字取自材料；结论 `evidence_ids` 全部命中 `evidence_map`。

## 3. 收尾（finalize + selfcheck）

```bash
python -B scripts/main.py finalize
python -B scripts/main.py selfcheck
```

- [ ] `finalize` 后 `/app/output/` 下存在 `result.json`、`review.md`、`screening_log.json`、`evidence_map.json`。
- [ ] `selfcheck` 输出 `error_count == 0`；有 error 则按 `checks[]` 的 `detail` 修复后重跑。
- [ ] `selfcheck` 的 `evidence_verbatim` 检查通过（全部 quote 逐字命中）；未命中则修正引用或降级 confidence。
- [ ] `status` 诚实：五环节完成且证据可回链 → `ok`；部分完成 → `partial`（列缺口）；材料不足 → `insufficient_evidence`；输入不可解析 → `input_error`。

## 4. 失败处理对照表

| 场景 | 处置 | 退出码 |
|---|---|---|
| 空输入目录/无可用材料 | `status=insufficient_evidence`，`pending_human` 列缺口，写合法 result.json | 0 |
| 坏 JSON / 坏编码 | 记 `errors`/`degradations`，继续处理其余材料 | 0 |
| 检索为空 | 检索块记 `not_run` 或空命中，`gaps` 落位，不伪造 hits | 0 |
| 全文不可得/仅摘要 | 标 `abstract_only`/`missing_fulltext`，结论降级 | 0 |
| 数据库/接口超时 | 记 `degradations`，块标 `not_run` | 0 |
| 条款冲突/证据矛盾 | 并列记录 + `undetermined` | 0 |
| 缺软件/缺权限 | 记 `limitations`/`errors`，降级为最小合法产物 | 0 |
| 任务中断 | 从 `run_state.json` 续跑，保留已落盘 partial | 0 |
| selfcheck 报 error（仅 `--strict`） | 修复后重跑 | 1 |

## 5. 交付前离线自检

```bash
python -B scripts/selftest.py    # 空输入 / 坏 JSON / 坏编码 / 完整案例 四场景
```

预期 `11 passed, 0 failed`（自检只验证 Skill 自身机制，不产生任何评分或基准结果）。

## 6. 最小调用示例（示意输出，非正式案例结果）

输入 `case.json` 含 `output_contract` 与材料后：

```bash
python -B scripts/main.py discover
# -> {"exists": true, "case_json": "/input/case.json", "task_md": "/input/task.md", ...}

python -B scripts/main.py skeleton
# -> {"case_id": "…", "wrote": true, ...}

# （Agent 完成 P1–P4，写入 /app/work/ 各文件）

python -B scripts/main.py finalize
# -> {"ok": true, "status": "ok", "conclusions": N, ...}

python -B scripts/main.py selfcheck
# -> {"pass": true, "error_count": 0, "warn_count": W, "checks": [...]}
```

以上输出为示意格式，不冒充任何正式案例结果。
