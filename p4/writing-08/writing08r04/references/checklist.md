# 降级判定表与失败模式清单

本文件用于收口前自查与异常处置。规则：**宁可诚实降级，不可静默编造**。

## 1. 降级判定表

| 场景 | 判定 | status | 必须写入的字段 |
|---|---|---|---|
| 材料齐全、六类要素可核验 | 成稿 | `ok` | manuscript/evidence_map/preserved_facts/gap_register 齐全，validate 通过 |
| 部分要素缺证据，成稿仍可用 | 成稿但有缺口 | `partial` | uncertainty 列缺口、limitations 列失效场景、pending_human 列追问 |
| 材料几乎为空或全不可读 | 无法成稿 | `insufficient_evidence` | limitations 列全部缺口，不生成示例答案 |
| case.json/task.md 损坏且无法恢复 | 输入错误 | `input_error` | errors 记录损坏详情 |
| 缺 pdftotext / Office 解析失败 | 降级继续 | 保持原判定 | degradations 记"未提取，改由 pdf-text-tool/office-renderer 处理" |

## 2. 失败模式清单（逐条避免）

1. **补数**：材料没写 epoch 数却写 `训练 10 个 epoch`。→ 写 `not_reported`。
2. **条件串台**：把验证集的 seed 套到训练集。→ train/val/test 各自独立溯源。
3. **单位漂移**：把 `0.3` 写成 `30%` 或把 `1e-4` 写成 `0.0001`。→ 数值与单位逐字复用。
4. **强度升降**：材料写"可能"，成稿写"显著"。→ 限定词不升不降。
5. **隐藏冲突**：config 与 log 的 batch_size 不同却只写一个。→ 并列记录 + `source_conflict`。
6. **套话限制**："本文仍有局限"。→ 必须给出 scope + condition + failure_scenario + mitigation。
7. **示例冒充结果**：把自造数值写进正式成稿。→ 示例必须显式标注。
8. **空节占位**：六类要素只留标题。→ 无内容写 `not_found` + 缺口，不留空话。

## 3. 自检命令

```bash
python3 scripts/selftest.py
python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
```

验收：`selftest` 退出码 0；`validate` 通过（`validation_report.json` 中 `passed=true`）；`result.json` 含 case_id、output_contract 全字段、status/sources/uncertainty/change_log；`evidence_map.json` 每条 locator+quote+verdict+confidence 非空。
