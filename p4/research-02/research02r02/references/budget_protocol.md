# 时间预算与早落盘协议

本 Skill 的核心设计是把不可见的"Agent 执行预算"变成可审计的分阶段协议。总预算固定为 **900 秒 / 64 次模型请求 / 每响应 ≤16384 token（含推理）**。

## 1. 阶段预算（硬截止）

| Phase | 名称 | 截止秒 | 请求上限 | 产物 |
|---|---|---|---|---|
| 0 | 骨架落盘 | 180 | 6 | result.json(partial) + plan.json |
| 1 | 盘点筛选 | 360 | 12 | inventory.json + screening_log.json |
| 2 | 证据主题 | 600 | 24 | evidence_table.jsonl + themes.md |
| 3 | 草稿冲突 | 840 | 20 | review.md + conflicts_and_gaps.md |
| 4 | 自检定稿 | 900 | 2 | validate + status 定稿 |

`scripts/main.py` 会把这套计划写成 `/app/work/plan.json`，含每阶段 `status` 与 `finished_at`；Agent 每阶段开始/结束用 `main.py` 的 `touch_phase` 逻辑追加检查点（也可直接编辑 plan.json）。

## 2. 早落盘（Phase 0 铁律）

- 第 0 阶段**唯一目标**是产出一版合法 `result.json`：`case_id` + 全部契约字段（缺值 `null`/`[]`）+ `status=partial` + 空 `sources/uncertainty/change_log`。
- 此后任何一步失败都不允许覆盖这份骨架；只向 `change_log`/`errors` 追加，保证 15 分钟结束时磁盘上一定有可打开的结果。
- `input_error` 只在 case.json 缺失或不可解析时使用；材料不足用 `insufficient_evidence`，材料齐全但没跑完用 `partial`。

## 3. 预算护栏（逼近上限时的收敛规则）

1. 每次发起模型请求前，先读 `/app/work/plan.json` 的 `last_updated` 与检查点，估算剩余时间/请求数。
2. 若已用 ≥62 次请求，或距 900 秒不足 30 秒：**停止任何新的实质分析**，直接执行 `validate_result.py`，把现有产物定稿。
3. 定稿优先级：先保证 `result.json` 合法 → 再补 `sources`/`uncertainty` → 最后才补草稿细节。宁可 `partial` 且缺口写清，不可超时留空产物。

## 4. 增量写盘纪律

- 每个 Phase 结束都落盘该阶段产物，禁止最后一次性倾倒。
- `change_log` 每项含 `ts / phase / action / detail / files`，使评审能重建"何时写了什么"。
- 同一输入两次运行，契约字段名与关键产物结构必须一致；时间戳可以不同，但内容路径不得漂移。

## 5. 与评分维度的对应

- 早落盘 → 运行稳定性、异常处理（超时/空产物兜底）。
- 阶段预算 → 效率增益与增益可验证性（预算使用可审计）。
- 护栏收敛 → 在逼近预算时保住可复核的 partial 结果，而非崩溃或空产物。
