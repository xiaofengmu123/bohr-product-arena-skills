# 时间预算与早落盘协议（time_budget）

本版本的核心设计取舍：**把 900 秒 / 64 次模型请求 / 16384 tokens 每回复，显式拆成阶段
预算，并强制前 3 分钟先落盘一版合法 partial 结果骨架，之后只做增量改进。** 宁可交不完整
但合法的结果，不零产物。

## 1. 硬预算

| 项 | 值 |
|---|---|
| 总时长 | 900 秒（15 分钟） |
| 模型请求 | 64 次（含 reasoning） |
| 每回复 tokens | ≤ 16384 |
| 案例数 | 3 个固定案例 |

## 2. 阶段预算表（按单案例，脚本命令为确定性秒级）

| 阶段 | 命令 | 产出 | 时间 | 模型请求 |
|---|---|---|---|---|
| ① 早落盘骨架 | `init` | `/app/output/result.json`（partial）+ `/app/work/index.json` | **≤3 分钟** | ≤4 |
| ② 材料清单 | `survey` | 文件+工具清单 | ≈1 分钟 | ≤2 |
| ③ schema 探查 | `inspect` | 数据字典+每表列类型 | ≈1 分钟 | ≤2 |
| ④ 任务解读 | 读 task.md/case.json | 分析计划（设计/单位/假设） | ≈2 分钟 | ≤4 |
| ⑤ 确定性计算 | `analyze` | analysis.json+cleaned CSV+figures+quality_checks | 秒级 | 1-2 |
| ⑥ 解释补写 | 依据 ⑤ | result.json 的 interpretation/uncertainty | ≈3 分钟 | ≤10 |
| ⑦ 汇总+自检 | `build` + `selfcheck` | 全部产物+校验 | ≈2 分钟 | ≤3 |
| 预留 | — | 失败重试、scipy 交叉核对、事后多重比较 | ≈3 分钟 | 剩余 |

合计约 15 分钟 / 约 26 次请求，为失败与复核留出余量。

## 3. 早落盘协议（本版重点）

1. **≤3 分钟内**：`python scripts/main.py init` 必须完成，写入
   `/app/output/result.json`：`case_id`（从 case.json 原样复制）、`status=partial`、
   output_contract 全部键回填（缺值 `null`/`[]`）、`sources`（已发现的文件）、
   `uncertainty`、`change_log`（init 记录）、`errors/degradations/pending_human/runtime`。
2. **原子写盘**：先写 `.tmp` 再 `os.replace`，任何中途异常都不留半截 JSON。
3. **增量改进**：之后每步只合并新数据到同一 result.json，**不重写、不丢失先前字段**；
   `analyze` 补 descriptive/inferential/effects/quality，`build` 定稿 status 并复制代码。
4. **checkpoint 纪律**：每完成一步立即 `save()`；解释写一段就 `build` 一次刷新 status。

## 4. 请求与 token 纪律

- 脚本命令不消耗模型请求；把可确定计算全部交给脚本（描述统计、检验、效应量、CI）。
- 模型请求只用于：读材料、定设计、写计划、交叉核对、写解释、写不确定性。
- 单次回复超 16384 tokens 前先 `build` 落盘，再分批发解释。
- 正态性 p 值优先 `scipy.stats.shapiro`（scientific-python 已声明），不重复手算。

## 5. 时间耗尽时的保底

若时间不足，停止新步骤，立即执行 `build` + `selfcheck` 把已有中间结果固化为合法
result.json；`status` 如实标 `partial`，`uncertainty` 列缺口，`errors` 记中断原因。
**绝不编造未完成的数字，也绝不交零产物。**

## 6. 与参考包的差异（设计取舍）

- 参考包把早落盘写为流程第①步；本版把整个 900s/64 请求**显式拆成阶段预算表**，并为每步
  给出请求上限与失败余量，使"早落盘"成为可执行的预算约束而非口号。
- `init` 的骨架由脚本**原子生成并立即含 output_contract 全键回填**，Agent 只做增量合并，
  减少人工遗漏契约键的风险。
- 每次 `save()` 都更新 `runtime.finished_at`，`selfcheck` 回写诊断，形成可追踪的进度证据。
