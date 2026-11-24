# 鲁棒性与降级矩阵（robustness）

本 Skill 的差异化侧重点：**任何输入都不崩溃、都产出合法 result.json、退出码 0**。
所有脚本只用标准库，不依赖 matplotlib/CJK 字体即可运行骨架与自检。

## 1. 降级矩阵

| 场景 | 处置 | status | 产物 |
|---|---|---|---|
| 空输入目录 | 记录 `input_dir_missing`，写最小合法结果 | `input_error` | result.json |
| 无任何可识别材料 | 列缺口，不编造数值 | `insufficient_evidence` | result.json + pending_human |
| 坏 JSON（case.json） | 捕获到 errors/uncertainty，继续处理 task.md | `partial` | result.json |
| 坏编码文本 | utf-8-sig→utf-8→gb18030→latin-1 逐级降级 | 按材料 | result.json |
| 单文件 >2MB | 跳过并记 `file_too_large(skipped)`，不读半行 | 按材料 | result.json |
| 缺 output_contract | 从正文表格反推；再不行用最小契约兜底 | `partial` | result.json + pending_human |
| 缺 matplotlib/CJK 字体 | 用可用子集（纯文本统计+数据表），记 limitations | `partial` | result.json + degradations |
| 输出目录写盘失败 | 降级写到 work/result.fallback.json，进程不崩溃 | `partial` | fallback.json |
| 查询/绘图失败 | 记 failed_queries/anomalies，保留原始错误信息 | `partial` | result.json |

## 2. 硬性要求

- 退出码恒为 0；仅 `check --fail` 且存在 FAIL 项时退出 1（用于 CI，Harness 不使用）。
- 任何未捕获异常都落到 main.py 的兜底，打印 ERROR 但返回 0。
- 不编造数据、不编造软件状态、不编造评测分数；缺失写 `pending_human` 或
  `not_reported`，绝不用示例填充正式案例。
- 未知的契约键不改名、不删除、不改层级；无法取值给 `null`（列表给 `[]`）。

## 3. 复现性保障

- init 即写 `work/manifest.json`：输入文件哈希 + 随机种子 + Python 版本 + 文件清单。
- 两次运行对比 manifest 与 result.json 的 case_id 应一致；不一致即复现性缺口，
  写入 uncertainty。
- 固定随机种子、固定类别排序、固定分面布局；图形与统计重跑一致。

## 4. 自检口径（scripts/selftest.py）

运行 `python3 -B scripts/selftest.py`，覆盖：空输入、缺输入目录、坏 JSON、超大文件、
坏编码、缺参数、正常链路契约回填、产物格式（SVG/PDF/PNG≥300dpi）、重复运行一致性。
自检是诊断工具，退出码恒为 0。

`check` 输出的级别语义：

| 级别 | 含义 | 处置 |
|---|---|---|
| PASS | 该检查项通过 | 无需动作 |
| WARN | 存在风险但不阻断交付（如 PNG 缺 pHYs、缺 pending_human） | 记 degradation 后补 |
| FAIL | 结构/产物不合契约（如缺 case_id、契约键缺失、产物损坏） | 补字段或重新导出 |

`check` 默认退出码 0；仅 `check --fail` 且存在 FAIL 项时退出 1，供 CI 使用。
Harness 运行时不要用 `--fail`，确保任何质量缺口都不中断进程。

## 5. result.json 字段语义（复核口径）

- `status` 只反映"当前可交付程度"，不是题目得分；`ok` 仅在契约字段全部回填、图注
  与证据齐全、自检无 FAIL 时设置。
- `sources` 是逐字证据，`uncertainty` 是尚未解决的不确定项，`pending_human` 是必须
  人工确认才能定案的事项，三者不互相替代。
- `errors` 记录已捕获的失败（读文件、写盘、查询、绘图），`degradations` 记录主动降级
  的取舍（跳过超大文件、缺字体改用英文、缺库改用数据表），两者区分"失败"与"取舍"。
