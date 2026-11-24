# 自检清单与健壮性核对表（提交/交付前必查）

## 1. 六点覆盖自检（对应评分维度，逐条勾选）

- [ ] **问题拆解**：`decomposition.md` 含 ≥2 个 `## SQn` 子问题，且标注依赖边（前置/可并行）。
- [ ] **检索计划**：`search_plan.md` 写清检索顺序、来源范围、停止条件；无重复查同一批文献。
- [ ] **证据回链**：每条结论 `evidence_ids` 命中 `evidence_matrix`；每行证据有 `locator + quote` 且逐字取自材料。
- [ ] **冲突证据**：`uncertainty.conflicts` 逐条并列两个来源 + 解决说明，未静默择一。
- [ ] **综合结论**：`research_product.conclusions` 非空，与 `question`、证据范围一致。
- [ ] **研究空白**：`uncertainty.gaps`/`pending_human` 写出具体“缺什么”，不用套话。

运行 `python3 -B scripts/main.py selfcheck` 可自动打印六点通过/未通过与 `status`。

## 2. 健壮性核对表

| 场景 | 要求 | 验证方式 |
|---|---|---|
| 空输入目录 | 退出码 0，写合法 `insufficient_evidence`，不抛异常 | `selftest` 场景 1 |
| 坏 JSON / 坏编码 | 记入 `errors`/`degradations`，继续处理其余材料 | `selftest` 场景 2 |
| 缺参数/未知参数 | `--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work` | `main.py --help` |
| PDF 无文本层 | `judgment=extraction_failed`，建议 OCR，不硬读像素 | 手工核对 |
| 证据互相冲突 | 并列记录 + `undetermined`，不强行取一方 | 手工核对 |
| 写盘失败/缺权限 | 降级为最小合法产物，进程不崩溃 | 手工核对 |
| 重复运行 | 同一输入两次结果一致（时间戳字段除外） | `selftest` 场景 4 |

## 3. 最小调用示例（示意，非正式案例）

```bash
# 1. 发现输入
python3 -B scripts/main.py discover
# 2. 3 分钟内先落盘骨架（status=partial）
python3 -B scripts/main.py skeleton
# 3. 逐条追加证据卡（必须 locator + 逐字 quote）
python3 -B scripts/main.py card --card '{"id":"E1","subquestion":"SQ1", \
  "source":"materials/a.pdf","locator":"p3 §2.2","quote":"…逐字引用…", \
  "claim":"…","judgment":"supports","reason":"…","conclusion_ids":["C1"]}'
# 4. 写 product/conclusions/uncertainty 后组装
python3 -B scripts/main.py finalize
# 5. 自检
python3 -B scripts/main.py selfcheck
```

示意输出（仅为说明接口，标注为示例，不冒充正式案例结果）：

```text
{"ok": true, "status": "ok", "conclusions": 1, "evidence": 1,
 "referential_errors": 0, "errors": 0, "runtime_seconds": 1.2}
```

## 4. 降级决策表

| 信号 | 处置 | status |
|---|---|---|
| 无材料 / 无 case.json | `pending_human` 列缺口，不编造 | `insufficient_evidence` |
| case.json 不可解析 | 记 `errors`，契约回退默认 | `input_error` |
| 有证据但缺结论/摘要/引用错误 | 写出 partial + 记 `errors` | `partial` |
| 结论+证据自洽且无引用错误 | 正常组装 | `ok` |

## 5. 交付前总检

1. `python3 -B scripts/selftest.py` 输出 `14/14 passed`。
2. `result.json` 能被 `json.load` 解析，含 `case_id/status/sources/uncertainty/change_log`。
3. 所有契约字段已回填，未知键未删、未改名。
4. 每个关键结论可回链到证据卡的 `locator + quote`。
5. 无预写答案、无伪造分数、无凭空参考文献。
