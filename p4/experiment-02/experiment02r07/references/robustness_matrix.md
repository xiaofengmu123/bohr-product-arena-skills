# 实验方案可开工性审计 — 鲁棒性与降级矩阵

> 本变体的设计重心：**空输入、坏 JSON、缺参数、超大文件都要优雅降级、退出码 0、
> 不留半成品、且每次降级都有据可查**。脚本为确定性实现，同一输入两次运行同 run_id。

## 1. 故障 → 检测 → 处置 → 记录 矩阵

| # | 场景 | 检测方式 | 处置 | 记录位置 | 产物状态 |
|---|---|---|---|---|---|
| 1 | 输入目录不存在 | `os.path.isdir` | 写 input_error 骨架 | `errors[].code=INPUT_DIR_MISSING` | `input_error` |
| 2 | 输入目录为空 | 文件清单为空 | 写 insufficient 骨架 | `status_reason` | `insufficient_evidence` |
| 3 | 坏 JSON / 截断 JSON | 解析链严格→规范化→截断修复→JSONL | 修复；失败则跳过该文件 | `degradations[].code=JSON_REPAIRED/PARSE_FAILED` | `partial` 或 `insufficient_evidence` |
| 4 | 尾逗号/弯引号/控制字符 | 正则规范化 | 去尾逗号、smart-quote 替换、控制字符替换 | `JSON_NORMALIZED/JSON_CONTROL_CHARS` | 继续 |
| 5 | BOM / UTF-16 / UTF-32 | BOM 嗅探 | 按 BOM 解码；BOM 优先于 NUL 密度判定 | `file.decode` | 继续 |
| 6 | GB18030/Big5/cp1252 | 解码链 | 顺序尝试，最后 replace 兜底 | `DECODE_REPLACED` | 继续 |
| 7 | 超大文件（>2MB） | 大小比较 | head+tail 分段读取，不整体载入 | `TRUNCATED_READ`（含总字节与策略） | 继续，缺尾部字段标 limitation |
| 8 | 二进制/图片/PDF | 扩展名 + NUL 密度 | 文本类跳过；PDF 用 pdftotext 提取（缺工具则标注） | `BINARY_SKIPPED / PDF_TOOL_UNAVAILABLE` | 继续 |
| 9 | 符号链接 | `os.path.islink` | 跳过（Harbor 禁 symlink） | `SYMLINK_SKIPPED` | 继续 |
| 10 | 输出/工作目录不可写 | `write_json` 捕获 OSError | 降级到 stdout 输出最小合法 JSON | `degradations[].code=WRITE_FALLBACK` | 退出码 0 |
| 11 | 文件数/深度超限 | 计数上限 | 截断目录遍历，记录上限 | `FILE_LIMIT_REACHED` | 继续 |
| 12 | 单个文件读失败 | try/except OSError | 跳过该文件继续 | `READ_FAILED / STAT_FAILED` | 继续 |

**不变量**：任何单点异常都不允许向上抛未捕获异常；`main` 永远 `return 0`
（除 selftest 自检本身按 FAIL 数返回 1，用于 CI/交付把关）。

## 2. 降级代码速查

| code | 含义 | 是否阻断 |
|---|---|---|
| `INPUT_DIR_MISSING` | 输入目录缺失 | 是（input_error） |
| `TRUNCATED_READ` | 文件超大分段读取 | 否，缺尾部需人工复核 |
| `JSON_REPAIRED` | 截断 JSON 被平衡修复 | 否，需在 uncertainty 标注修复 |
| `PARSE_FAILED` | JSON 无法修复 | 是（若无其他 case） |
| `DECODE_REPLACED` | 兜底替换解码 | 否 |
| `PDF_TOOL_UNAVAILABLE` / `PDF_EXTRACT_FAILED` | PDF 文本未提取 | 否，证据缺口写 pending_human |
| `BINARY_SKIPPED` | 二进制跳过 | 否 |
| `SYMLINK_SKIPPED` | 符号链接跳过 | 否 |
| `WRITE_FALLBACK` | 写盘失败走 stdout | 否（产物仍 JSON） |

## 3. 编码与解析链（ingest.py 固定顺序）

1. BOM 嗅探：`utf-8-sig / utf-32 / utf-16-le / utf-16-be`。
2. 无 BOM 且 NUL 密度 >20% → 先试 `utf-16-le/be`。
3. 其余按 `utf-8 → gb18030 → big5 → cp1252` 顺序，最后 `utf-8 replace` 兜底。
4. JSON：`strict → control-stripped → normalized（去尾逗号/弯引号）→
   repaired（闭括号/截尾/丢半键）→ jsonl`。
5. 二进制判定在 BOM 之后：BOM 为 UTF-16/32 的文本不按二进制误判。

## 4. 单位表（audit.py 分钟基准）

| 单位 | 分钟 | 备注 |
|---|---|---|
| s / sec / second | 1/60 | |
| min / minute / 分 / 分钟 | 1 | |
| h / hr / hour / 小时 / 时 | 60 | |
| d / day / 天 / 日 | 1440 | |
| wk / week / 周 / 星期 | 10080 | |
| mo / month / 月 | 43200 | **近似 30 天，需标 approx** |
| y / year / 年 | 525600 | **近似 365 天，需标 approx** |
| wd / 工作日 | 480 | **近似 8 小时，需标 approx** |

金额解析支持 `€ $ £ ¥ ￥` 与 `k`（千）、`万` 后缀；`"2–3 h"` 取区间上界作计划值并标注。

## 5. 执行预算协议（900s / 64 请求 / 16K token）

- **硬规则**：前 3 分钟内先运行 `ingest.py` 落盘 `result.json` 骨架，之后只做增量改进。
- 每步结束 `--merge` 写回并自动追加 `change_log`，任何一步中断都不丢已完成部分。
- 报告由脚本渲染，模型不手写大段 MD；算术批量走 `--calc`，不口头估数。
- 预算耗尽前至少保证 `partial` 产物合法且缺口已写入 `uncertainty/pending_human`。

## 6. 契约绑定规则

- `output_contract` 递归查找（键名含 `output_contract`），支持 `fields`/`properties`
  两种写法；字段类型由说明/默认值/type 推断：`array→[]`、`object→{}`、`string→""`、
  `number/boolean/null→null`。
- 回填到顶层；与内置键冲突的字段记入 `kept_builtin` 不覆盖；未填字段记 `unfilled`。

## 7. 确定性承诺与自检

- `run_id = sha256(skill_version + case_fingerprint)`，指纹来自文件内容哈希；无墙钟时间。
- 报告与 result.json 同源（渲染自同一 JSON），杜绝两处漂移。
- `selftest.py --faults` 内置 11 项故障注入（空输入、缺目录、坏 JSON、UTF-16、
  GB18030、超大文件、二进制、不可写输出、确定性复跑、符号链接、契约类型），
  交付前与每次大改后必跑，全部 PASS 才算可用。
