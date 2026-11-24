# 健壮性与自检规范

本文件规定异常处置矩阵、降级阶梯、确定性要求与自检步骤，供 Agent 在运行时对照执行。

## 1. 异常处置矩阵

| 场景 | 要求 | 落点 |
|---|---|---|
| 空输入目录 | 退出码 0，写合法 `result.json`（`insufficient_evidence`），列出缺口 | `uncertainty`/`pending_human` |
| 坏 JSON / 坏编码 | 捕获并记录，继续处理其余材料，不崩溃 | `errors`/`degradations` |
| 缺参数/未知参数 | `--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work` | 脚本默认值 |
| 缺 `pdftotext` | 记入 `degradations`，用已抽取文本继续，不伪造解析结果 | `degradations` |
| 写盘失败 | 降级到最小合法产物，不让进程崩溃 | 脚本内部 try/except |
| 单文件超限 | 跳过并记录（默认上限 2MB） | `errors`/`degradations` |
| 重复文献 | 按 DOI/题名/内容哈希去重，去重记录写明 keep/drop/basis | `screening_log.json` |
| 元数据冲突 | 两源都记录并标 `source_conflict`，不静默择一 | `evidence_table`/`sources` |
| 结论冲突 | 并列呈现并标 `disputed`，不抹平 | `review.md`/`conflicts_and_gaps.md` |
| 检索为空/无相关文献 | `insufficient_evidence`，缺口落到具体证据缺口 | `uncertainty` |

## 2. 降级阶梯（诚实标注，禁止编造）

`ok` ← 四步完成且每条结论可回链；`partial` ← 部分完成，列缺口；`insufficient_evidence` ← 材料不足以回答问题；`input_error` ← 输入不可解析。

- 仅摘要 → 该篇 `abstract_only`，结论降为 `suggestive`/`gap`，不补齐正文数据。
- 未报告字段 → `not_reported`；绝不虚构文献、DOI、数值、评测分数或运行结果。
- 材料不足时输出"材料不足 + 缺口清单"，不用示例填充。

## 3. 确定性与幂等

- 脚本只用 Python 标准库；固定输入 → 固定输出（同序、同值），不依赖随机数或时间戳以外的排序。
- `change_log`/`runtime` 中的时间戳是唯一允许随运行变化的部分。
- 重跑 `main.py` 幂等：同材料文件重新抽取得到相同 `extracted/*.txt` 与相同 SHA-256。

## 4. 自检步骤（提交/交付前）

```bash
python3 -B scripts/selftest.py        # 空输入 / 坏 JSON / 最小契约 三场景
python3 -B scripts/main.py --help     # 退出码 0
python3 -B scripts/validate_result.py --help
```

每个案例运行结束前执行：

```bash
python3 -B scripts/validate_result.py --output /app/output --case /input/case.json
```

人工复核要点：抽 3 条 `[S#]`，回到 `evidence_map` 的 quote 与 `evidence_table` 的原文，核对逐字一致、强度一致、locator 可定位。

## 5. 运行时预算与检查点

总预算 900 秒、64 次模型请求、每响应 ≤16384 token。Phase 0 只花 1 次请求（脚本调用），把剩余请求留给分析与修复。

| Phase | 时间窗 | 关键检查点 | 落盘产物 |
|---|---|---|---|
| 0 | 0–3 分钟 | result.json 已存在且为合法 JSON | result.json（partial）、inventory、抽取文本 |
| 1 | 3–7 分钟 | 筛选标准写在决定之前 | screening_log.json |
| 2 | 7–11 分钟 | 每条证据有 quote；每 claim 有 quotes | evidence_table.jsonl、evidence_map.json、themes.md |
| 3 | 11–14 分钟 | 每个 [S#] 有 claim；冲突并列；空白有据 | review.md、conflicts_and_gaps.md |
| 4 | 14–15 分钟 | validate 通过或缺口如实标注 | 定稿 result.json |

请求纪律：读文件一次读全，不重复全量重读；写产物增量追加，禁止最后一次性倾倒；超上下文时先落盘已完成部分，再在 `uncertainty` 标注未覆盖材料。

超时/请求耗尽处置：若第 14 分钟仍未完成，把已完成产物与 `status=partial` 落盘，并在 `uncertainty`/`pending_human` 列出未完成项；不得为凑 `ok` 而省略溯源。

