# 预算台账与检查点协议（budget-protocol）

本文件说明 sci-viz-checkpoint 的“早落盘 + 增量改进”纪律，是 SKILL.md 第 4、5 节的机器可读依据。`result.json` 的 `budget` 字段是本协议的持久化副本。

## 1. 预算拆分

| 阶段 | 时间窗 | 时间预算 | 请求预算 | 门槛（gate） |
|-|-|-|-|-|
| A 骨架落盘 | 0–3 分钟 | 180 s | ≤4 | `result.json`(partial) + `work/plan.md` 已写盘 |
| B 主分析 | 3–12 分钟 | 540 s | ≤45 | 清洗/统计/选图/编码/标注/导出逐项落盘 |
| C 收尾自检 | 12–15 分钟 | 180 s | ≤10 | `check` 通过，`status` 与 `runtime.finished_at` 已更新 |
| 保留 | — | — | 5 | 仅用于失败恢复 |

总预算 900 s / 64 次请求。

## 2. 检查点协议

每个阶段结束前执行一次 checkpoint：

- 阶段 A：`init` 写 `result.json` + `work/plan.md` + `work/ledger.json`；此时 `status=partial`，所有契约字段为 `null/[]`。此后任何崩溃都有合法产物。
- 阶段 B：每个子步骤（清洗/统计/选图/编码/标注/导出）至少 `commit` 一次，带 `--phase B`，写 `change_log` 并更新 `budget`。
- 阶段 C：`check --require-contract`，补齐后 `commit --status ok/partial`，更新 `runtime.finished_at/seconds`，再 `check` 复核。

## 3. 一次请求一件事

- 读文件、跑统计、画图、导出、写图注各占一次请求，先读后写。
- 中间产物一律落 `work/`，最终产物（图件、代码、图注、`result.json`）才进 `/app/output/`。
- 不把“读大文件 + 画图 + 导出”合并成一次含糊动作，以免预算失控。

## 4. 越界与降级处理

- 阶段 B 达到 45 次请求或总时长接近 900 s：立即停止扩展，把未完成项写进 `uncertainty`，保留已落盘 partial 结果。
- 越界不得伪造 `done`；未完成阶段保持 `done=false`，`degradations` 如实标注 limitations。
- 保留的 5 次请求只用于中断恢复或补救关键缺项，不用于锦上添花。

## 5. 与 result.json 的关系

- `budget.phases[].done` 与 `recorded_at` 记录各阶段门槛是否达成，`budget.elapsed_seconds` 记录累计用时。
- `ledger` 子命令读取 `budget` 显示剩余预算，供 Agent 判断是否降级。
- 每次 checkpoint 同时写入 `change_log`，每项带 `phase` 字段，便于审计“哪一步在哪个阶段完成”。
