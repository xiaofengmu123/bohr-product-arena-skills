# 阶段预算与早落盘规范

对应评分维度「可靠体验（调用与易用性 / 运行稳定性 / 异常处理）」与「实际效果（结果可核验性）」。本文件把 900s / 64 请求拆成可检查的阶段预算，并规定「前 3 分钟先落盘 partial 骨架」。

## 1. 总预算与分配

- 总时长 900s，模型请求 ≤64 次，每次响应 ≤16384 token（含推理）。
- 下表共分配 52 请求 / 850s，**保留 12 请求 / 50s 余量**用于重试、修复与恢复。

| 阶段 | 动作 | 时间预算 | 请求预算 | 强制落盘物 |
|---|---|---|---|---|
| S0 | 骨架 | ≤170s | ≤6 | result.json / contract.json / state.json / reproduce.json |
| S1 | 读材料 | ~120s | ≤8 | work/notes.md |
| S2 | 数据解析 | ~120s | ≤8 | work/t_*.json |
| S3 | 出图 | ~200s | ≤14 | 可编辑 SVG + PNG/PDF |
| S4 | 校验 | ~120s | ≤8 | mapping.json + 校验记录 |
| S5 | 自检 | ~60s | ≤4 | work/check_report.json |
| S6 | 落盘 | ~60s | ≤4 | 最终 result.json |

## 2. 早落盘规则（强制）

1. 开始后**先运行 `python scripts/main.py`**，在 3 分钟内得到 `/app/output/result.json`：status=partial、契约字段全 null、change_log 首条。
2. 骨架不依赖任何图面成果，因此永不因缺数据/缺软件而失败；随后每一步只做增量改进，随时可中断。
3. 每个阶段结束时推进 `work/state.json` 的 `stage`，把命令追加到 `work/reproduce.json`，把改动追加到 `change_log`。

## 3. 预算触发降级

- 任一阶段耗时或请求数超过预算：记录到 `result.json` 的 `degradations`，立即进入 S6 保底落盘。
- 同一策略连续失败两次：停止重试，换策略（如标准库手写 SVG 替代 matplotlib），或降级为「最小可复核结果」。
- 「最小可复核结果」= result.json（case_id + status + sources + uncertainty + change_log）+ 每个契约字段要么有产物/对象、要么 null 且注明原因 + 至少一个可编辑 SVG 骨架 + 来源与修改说明。

## 4. 可复现（reproduce manifest）

`work/reproduce.json` 固定 `seed=0` 并记录：

- 输入文件相对路径 + SHA256 + 字节数（材料版本可核对）；
- 每条已执行命令（脚本名 + 参数），他人按顺序重跑；
- Python 版本号。

绘图侧同步要求：`numpy.random.seed(0)`、`matplotlib.rcParams` 固定字号/线宽/dpi、字典序排序分组、固定 figsize；同一输入必须得到同一张图。重跑对比用 SHA256 校验 PNG/SVG/PDF，不一致即视为不可复现。

## 5. 时间戳与恢复

- 所有 `time_utc` 用 UTC ISO-8601（`%Y-%m-%dT%H:%M:%SZ`）。
- 中断恢复：读 `work/state.json` 的 `stage`，只重做该阶段未完成部分，不重做已完成阶段。
