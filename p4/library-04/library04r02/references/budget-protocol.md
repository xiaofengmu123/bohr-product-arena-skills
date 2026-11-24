# 阶段预算与早落盘协议

本文件是 SKILL.md 第 4 节的执行细则，把 900 秒 / 64 次模型请求拆成可检查的阶段预算，并规定“早落盘 + 单调增量改进”，这是本 Skill 相对“跑完才写结果”方案的核心设计取舍。

## 1. 设计取舍

- **先保证有合法产物，再追求完整**：任何时候进程被中断，`/app/output/result.json` 都已是一份 JSON 合法、含 `case_id` 与 output_contract 全部键的 `partial` 骨架，不会留下空文件或半截 JSON。
- **单调增量改进**：`scaffold` 只写骨架；`inventory` 与 `finalize` 读取已有 result.json 后**合并** `sources/library/answers` 等字段，不覆盖未知键、不重建结构。越晚的阶段越完整，但任意时点都可交付。
- **阶段截止时间可复核**：`/app/work/clock.json` 记录唯一启动时刻，每个阶段用同一时钟判断 `elapsed ≤ deadline_abs_s`，结果写入 `budget.phase_deadlines_met`。

## 2. 阶段预算表

| 阶段 | 截止(累计) | 请求预算 | 落盘产物 | 超时处置 |
|---|---|---|---|---|
| 0 骨架 scaffold | 180s | ~5 | `result.json`(partial) + `case_summary.json` + `budget.json` + `clock.json` | 立即写 minimal partial 产物 |
| 1 盘点 inventory | 720s | ~20 | `inventory.json` + `evidence_index.json` + `extracted/*.txt` | `degradations` 记 `deadline_exceeded`，继续 |
| 2 问答 qa | 840s | ~15 | Agent 写 `/app/work/answers.json` | 缺 answers 时 finalize 定 `partial` |
| 3 汇总 finalize | 900s | 剩余~24 | `result.json` + `inventory.md` + `evidence_map.json` | `status=partial` + `budget_exceeded` |

## 3. 早落盘硬规则

1. 收到输入后的**首次模型请求**内执行 `python3 scripts/main.py scaffold`，用原子写（临时文件 + `os.replace`）落盘骨架；`change_log[0].action` 必须为 `scaffold_skeleton_written` 且时间戳 ≤ 启动后 180s。
2. 骨架即含 `case_id`、`status=partial`、空 `answers/sources`、完整 output_contract 键（缺值给 `null/[]/{}`）、初始 `change_log/runtime/budget`。这是“永远不会更差”的基线。
3. 后续每个阶段结束都原子回写 result.json 并追加 `change_log`，因此最新一份产物永远是完整可解析的。

## 4. 请求预算记账

Agent 每花一次模型请求执行 `python3 scripts/main.py mark --phase <阶段> --note <说明>`；`finalize` 读取 `/app/work/request_ledger.json` 汇总 `requests_used`，超过 64 次或 900s 时 `status=partial` 并写 `budget_exceeded`。请求计数由 Agent 自律维护，脚本只做汇总与如实标注，不伪造计数。

## 5. 自检与复现

- 早落盘可核验：查 `change_log[0]` 与 `runtime.started_at` 的差值 ≤ 180s。
- 确定性：`sources`、`library`、`answers` 均按相对路径/字典键排序；`reproducible_hash` 只取内容派生字段（case_id、contract 键集、sources 的 rel+sha256+type+size、library、answers 的 q/a/evidence、uncertainty），排除墙钟元数据 `runtime/change_log/budget`，故相同输入两次结果哈希一致。
- 交付前跑 `python3 scripts/main.py selftest`，三场景覆盖空目录、坏 JSON、正常样例（含重复文件、版本对、逐字引用核对）。

## 6. 边界

- 不把“骨架已写”当成“任务完成”：`status` 区分 `partial`（已盘点但问答未完成）与 `ok`（问答 + 引用核对 + 契约回填齐备）。
- 不隐瞒缺口：缺材料写 `insufficient_evidence`，缺软件写 `needs_ocr`/`unreadable`，契约未填充键进 `pending_human`。
