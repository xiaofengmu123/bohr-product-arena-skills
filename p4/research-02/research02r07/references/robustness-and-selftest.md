# 鲁棒性设计与自检（升级方向说明）

本变体把"不崩溃、不编造、可复现"放在首位，同时保持四步综述流程完整。所有降级都写入 `result.json` 的 `errors`/`degradations` 与 `change_log`，不会静默吞掉问题。

## 1. 鲁棒性场景与处置

| 场景 | 处置 | 产物状态 |
|-|-|-|
| 空输入目录 | 退出码 0，写合法结果，列出缺口 | `insufficient_evidence` |
| 坏 JSON / 坏编码 case.json | 捕获并记 `errors`，继续盘点材料；无材料时 | `input_error`（有材料则 `partial`） |
| 缺参数 / 未知参数 | `--input/--output/--work` 默认 `/input`、`/app/output`、`/app/work` | 正常骨架 |
| 超大文件（默认 >2MB） | 跳过正文读取，仅记录 SHA-256 与大小 | `degradations` 记录 |
| 二进制冒充文本 | 头部探测，跳过文本抽取 | `degradations` 记录 |
| 缺 `pdftotext` | 记入 `degradations`，用已抽取文本继续 | 不伪造解析结果 |
| 写盘失败 | 原子写盘；`/app/output` 失败则回退写 `work/result.json.fallback` | 退出码 2，不崩溃 |
| 重复运行 | 内容确定性：稳定排序、原子写盘、`\n` 换行；仅时间戳变化 | 结果一致 |

## 2. 设计取舍（与常见做法不同之处）

1. **先落盘、后分析**：`main.py` 在 2 分钟内写出一版合法 `result.json`，任何后续异常都不会留下空产物。
2. **诚实初始状态**：骨架状态随输入状况如实取 `partial`/`insufficient_evidence`/`input_error`，而非一律写 `partial` 再事后改。
3. **原子写盘**：所有 JSON/MD 先写同目录临时文件再 `os.replace`，崩溃不留半截文件。
4. **内容去重基于哈希而非文件名/题名**：同一 PDF 的不同命名副本只会保留首个，记录 `duplicate_of`。
5. **契约归一化**：支持 `{"properties":...}`、`{"fields":...}`、字段名→spec 映射、列表等形态；未知键不删、不改名，键名排序保证确定性。

## 3. 自检两种模式

```bash
# 校验一次真实运行的产物（生产自检）
python3 -B scripts/selftest.py --output /app/output --case /input/case.json

# 内置鲁棒性回归（空输入/坏 JSON/缺参数/超大文件/坏编码/重复运行确定性）
python3 -B scripts/selftest.py --unit
```

生产自检逐条覆盖：`result.json` 合法性与 `case_id/status/sources/uncertainty/change_log` 存在性、契约字段完整性、主产物齐全、`evidence_table.jsonl` 每行合法且字段齐全、综述 `[S#]` 标记可回链、`status=ok` 时无骨架残留且证据表非空。退出码 0 表示通过，1 表示存在失败项。

## 4. 边界

- 不做"通用大而全"Skill：只解决"多篇文献 → 可复核综述"这一类任务。
- 没有真实材料时输出 `insufficient_evidence` 并列出缺口，不用示例填充。
- 不负责单篇精读、全文翻译、语言润色、投稿选刊、事实层面学术审查。
- 脚本只用 Python 标准库；`pdftotext` 是唯一外部可执行文件，缺失时降级而非失败。
