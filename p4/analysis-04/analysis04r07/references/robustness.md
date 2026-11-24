# 鲁棒性设计说明（sci-analysis-exec-robust）

本变体把"鲁棒性"作为第一设计目标：**任何数据形态、任何参数缺失、任何环境缺件都不允许让进程崩溃或留下半成品**。所有入口函数不向上抛异常，异常一律转成结构化记录，退出码恒为 0。

## 1. 设计取舍（相对裸跑的差异）

| 取舍 | 具体实现 |
|-|-|
| 先落盘再分析 | `init` 在前 3 分钟落盘 partial 骨架；此后每一步都是增量，中断不丢结果 |
| 重入保护 | `init` 检测到已有合法 result.json 时不覆盖，只追加 change_log |
| 原子写 | 临时文件 + `os.replace`；失败降级直写，再失败写最小合法 result.json |
| 唯一事实源 | result.json 由 `log`/`set`/`trace` 类型安全地更新，Agent 不手改 JSON，避免损坏 |
| 编码回退 | `utf-8-sig → utf-8 → gb18030 → cp1252 → latin-1(替换)`，坏编码只降级不崩溃 |
| 大小/行数上限 | 文本 >5MB 跳过；CSV 扫描 >200k 行截断；快照单文件 >50MB 跳过 |
| 不假设结构 | 文件名/键名不固定，全部扫描发现；SQLite 只读访问；符号链接不跟随 |

## 2. 失败模式 → 处置策略

| 场景 | 识别 | 处置 | 记录字段 |
|-|-|-|-|
| 空输入目录 | `scan_input` 无文件 | status=insufficient_evidence，列缺口 | errors, uncertainty |
| 坏 JSON | `json.loads` 失败 | status=input_error，继续处理其余材料 | errors, degradations |
| 坏编码（GBK/BOM） | 按编码优先级回退 | 解码成功则继续，最终保底 latin-1 替换 | degradations(encoding) |
| 缺参数 | `--input/--output/--work` 未传 | 取 `SKILL_*_DIR` 环境变量，再取 `/input` `/app/output` `/app/work` | change_log |
| 未知参数 | `parse_known_args` | 忽略并继续，退出码 0 | - |
| 超大文件 | size 超限 | profile 跳过并标注 `file_too_large`；Agent 用 `head` 采样另存小文件 | degradations |
| 缺列/类型不符 | align 未匹配、列缺失 | 尝试别名匹配；仍失败转人工 | errors, pending_human |
| 依赖缺失 | import/调用失败 | 降级为纯标准库或 `.csv`/`.md` 文本产物 | errors, degradations, limitations |
| 查询为空 | 结果集 0 行 | 如实记录 `not_reported` 或空集，不静默跳过 | errors, sources |
| 数值溢出 | `sum/var` 溢出 | `_safe_mean_stdev` 返回 None | errors |
| 权限不足/写盘失败 | `makedirs/open` 失败 | 降级写最小合法 result.json，退出码仍 0 | errors |
| SQLite 损坏 | `connect/query` 失败 | 捕获并记录表级 error，不中断其他表 | per-table error |

## 3. result.json 字段语义

- `status`：`ok`（结果可核验）/ `partial`（进行中或缺项）/ `insufficient_evidence`（材料不足）/ `input_error`（输入无法解析）。
- `errors`：阻断性、需说明的错误（缺 case.json、缺 case_id、坏 JSON、产物路径不存在）。
- `degradations`：已降级但未阻断（编码回退、超大文件跳过、契约缺失）。
- `limitations`：方法或环境限制（缺依赖、无 PDF 解析器），与 `pending_human` 呼应。
- `sources`：关键结论逐字溯源，每项 `{locator, quote}`；冲突信息记录 `source_conflict`。
- `change_log`：每条含 `at/step/action`，由脚本自动追加，是增益可验证性的证据。

## 4. 常量一览

```
MAX_TEXT_FILE_BYTES = 5 MB      # 文本读取上限
PROFILE_ROW_CAP      = 200000   # CSV 扫描行数上限
MAX_SNAPSHOT_BYTES   = 50 MB    # 快照复制单文件上限
ALLOWED_STATUSES     = ok | partial | insufficient_evidence | input_error
```

## 5. 关键不变量

1. 任何子命令退出码恒为 0；错误通过 `errors/degradations/change_log` 传达，而非 shell 退出码。
2. result.json 始终是合法 UTF-8 JSON；即使最坏情况也至少含 `case_id + status + errors`。
3. 同一输入两次运行结果一致：脚本不含随机性；Agent 分析代码必须固定随机种子（`random.seed(42)` / `np.random.default_rng(42)`）。
4. 不依据目录名、文件名标签或记忆补造数据；一切数值以实际读到的文件为准。
