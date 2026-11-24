# 阶段预算调控与检查点协议（phase budget governor）

本文件是 `scripts/main.py` 中阶段预算与检查点机制的规则说明。它是本 Skill 的核心设计取舍：
**不把“早落盘”当建议，而是把每个阶段收尾都变成一次机械的检查点写入**，使任何中断都能
从最后一个检查点恢复，任何超预算都能触发“砍范围”而不是硬撑。

## 1. 预算表

总预算 900 秒、64 次模型请求、单响应 16384 token（含推理）。请求数由平台记录；脚本只追踪
墙钟时间与阶段推进，`--req` 参数让 Agent 把已用请求数并入提示。

| 阶段 | 名称 | 时间窗（秒） | 请求上限 | 退出条件 |
|-|-|-|-|-|
| P0 | 落盘骨架 | 0–180 | 6 | `/app/output/result.json` + `/app/work/state.json` 已写 |
| P1 | 盘点与字典 | 180–420 | 12 | `/app/output/data_profile.json` 已写 |
| P2 | 方案与代码 | 420–660 | 18 | 清洗/统计/可视化/查询代码已落盘 `/app/work` |
| P3 | 运行与核对 | 660–840 | 18 | 代码跑通且逐项复核完成 |
| P4 | 收尾与复核 | 840–900 | 10 | `validate` 通过或已记录 findings |

阶段时间窗由 `core.PHASE_BUDGET` 单一定义，`selftest.py` 校验其和等于 900 秒、请求和等于 64，
防止文档与实现漂移。

## 2. 检查点协议

每个阶段结束必须执行（从包根）：

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py checkpoint --phase P1 --note "..." \
  --work /app/work --output /app/output
```

`checkpoint` 做三件事，全部原子落盘：

1. 在 `/app/work/state.json` 追加 `{at, phase, note, requests_used}`，并把当前阶段前移；
2. 在 `/app/output/result.json` 的 `change_log` 追加一条记录；
3. 打印 `core.budget_status` 输出：当前阶段、时间窗、已用墙钟、是否超预算、剩余请求。

**恢复规则**：中断后读取 `state.json`，从最后一个 checkpoint 的阶段续跑；P0 之前的中断
视为“尚未开始”，重跑 P0 即可，因为 P0 是幂等的（覆盖式写骨架，不破坏已有字段）。

## 3. 越界规则

- 墙钟仍在时间窗内或超出 ≤20%：继续当前阶段。
- 超出时间窗 >20%（脚本输出 `OVER_BUDGET`）：立即“砍范围”——保留必交付项
  （清洗表、统计结果、图表、查询结果、`result.json`），放弃可选加分项，
  `status` 降为 `partial`，并在 `limitations` 写明被砍项与原因。
- 任何阶段超预算不得通过追加请求“硬试”：先写 checkpoint，再决定砍范围或转人工。

## 4. 为什么 P3 是“运行与核对”合流

调试与验证不是串行的两件事：每次修复（缺列、类型、编码、WHERE 条件、单位）之后都必须
立即用同一组校验重算，才能发现“修好 A 又弄坏 B”。因此本 Skill 把运行调试与结果核对放进
同一个 P3，要求“每修一处立即复核一次”，而不是先全部跑通再回头核对。这与把二者分开的
常见流程不同，是面向静默错误拦截的设计选择。

## 5. 确定性重跑

- 随机种子固定：`random.seed(42)`、`numpy.random.default_rng(42)`、`np.random.seed(42)`；
- 记录解释器版本、关键依赖版本（`pip show`/`importlib.metadata`）、命令行与退出码；
- 清洗规则写进 `/app/work/*.py` 与 `change_log`，他人可复算；
- 同一输入两次运行的 `result.json` 数值部分应一致（时间戳、日志顺序允许不同）。

## 6. 写盘降级链

`init` 与 `checkpoint` 使用 `core.write_json_fallback`：先写 `/app/output`，失败写
`/app/work`，再失败写当前目录，最后打印到 stdout——始终退出码 0 且保留最小合法
`result.json`。这保证“写盘失败”场景不会让进程崩溃，也不会静默丢结果。
