# 健壮性、状态赋值与确定性

本文件把 SKILL.md 第 8 节"降级策略"展开为可执行判定，对应评分维度"可靠体验"与"异常处理"。原则：**任何输入都产出合法 result.json，任何异常都显式说明，任何数值都不编造**。

## 1. 异常场景处置表

| 场景 | 处置 | 状态 | 记录位置 |
|---|---|---|---|
| 空输入目录 | 骨架 `status=insufficient_evidence`，列出缺口 | insufficient_evidence | errors |
| 输入目录不存在 | 最小合法 result.json | input_error | errors |
| case.json 坏 JSON | 降级解析，记 degradation，继续处理其余材料 | partial | degradations |
| 文本坏编码 | utf-8-sig→utf-8→gb18030→latin-1 逐级降级 | partial | degradations |
| 单文件 >2MB | 跳过并记录，不读入内存 | partial | degradations |
| CSV 无表头 | 自动生成 col1..colN，记 degradation | partial | degradations |
| CSV 字段错位/长短不一 | 短行补空、长行截断，记 degradation | partial | degradations |
| SQLite 打开失败 | 跳过该库，记 degradation | partial | degradations |
| 缺 matplotlib | 用纯文本统计 + 数据表代替图件，标注 limitation | partial | degradations |
| 缺 CJK 字体 | 标签改英文，标注 missing_cjk_font | partial | degradations |
| 输出目录不可写 | 降级写最小合法 result.json，进程不崩溃 | input_error | errors |
| 重复运行 | 固定排序、固定分位数、无随机采样，结果一致 | 与首次一致 | — |

## 2. status 赋值规则（诚实优先）

- `ok`：契约字段全部回填非空，图件已渲染导出，图注/证据/uncertainty 齐全，check 无 FAIL。
- `partial`：已完成部分步骤，或 check 存在 FAIL/WARN；必须写明已完成与未完成的边界，以及下一步缺口。
- `insufficient_evidence`：材料缺失或不足以得出任何结论；列出缺口，不编造数值。
- `input_error`：输入不可用（目录不存在、无任何可解析材料、写盘失败）。

禁止：为让 status=ok 而删除材料、改数值、伪造图件或隐藏异常。`partial` 本身是合法且诚实的状态，且必须在 `uncertainty` 写明 `pending_human` 缺口。

## 3. 确定性规则（两次运行结果一致）

- 不引入随机采样；抽样（如需）固定 `random.seed(0)`。
- 列序、行序、图例顺序按数据字典或首现顺序固定，不依赖 dict 迭代序（Python 3.7+ 有序但显式排序更稳）。
- 分位数用 `statistics.quantiles`（method='exclusive' 默认）；标准差用样本标准差（`statistics.stdev`，n−1 分母），并在图注/IR 写清分母约定。
- 清洗判定（IQR/z-score）阈值写进 IR，不随运行改变。
- 布局与分面顺序按字段排序，图例按类别值排序。

## 4. 结果可核验性要求

- 每个关键数值/图元一条 `sources` 记录：`{locator, quote, kind}`，`quote` 必须是材料逐字片段，不改写。
- 冲突证据并存，标 `source_conflict`；不隐藏、不选边。
- 缺失引用、DOI、数值一律写 `pending_human` 或 `not_reported`，不虚构。
- 图件与 `sources` 之间用 FigureIR 的 `evidence_map` 双向对应：图上的每个编码都有 source，result.json 的每条 source 都能指回图的某个元素。

## 5. 自检方式

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --help                     # 退出码 0
python3 -B scripts/selftest.py                        # 空目录/坏 JSON/缺契约全通过，退出码 0
python3 -B scripts/main.py check --output /app/output --require-contract
```

自检必须覆盖：空目录、坏 JSON、缺契约、无表头 CSV、SQLite、缺参数（用缺省 /input、/app/output、/app/work）。

## 6. 最小合法 result.json 模板（写盘失败/极端降级时的兜底）

任何情况下都要保证退出码 0 且 `/app/output/result.json` 是合法 JSON。极端降级时至少包含：

```json
{
  "case_id": "unknown",
  "status": "input_error",
  "sources": [],
  "uncertainty": [{"item": "pending_human", "detail": "材料不足或运行失败，需人工确认"}],
  "change_log": [{"ts": "2026-01-01T00:00:00Z", "seq": 0, "step": "fallback",
                   "action": "降级落盘最小合法 result.json", "files": ["result.json"]}],
  "errors": [],
  "degradations": [],
  "runtime": {"started_at": "2026-01-01T00:00:00Z", "seconds": null, "finished_at": null},
  "artifacts": []
}
```

`scripts/main.py init` 在发现输入目录不存在、写盘失败时自动走此路径；`scripts/ir.py` 的 `write_minimal_result` 是最终兜底函数，不抛异常。
