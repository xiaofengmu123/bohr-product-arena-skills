# 降级与局限（references/limitations.md）

本文件定义异常处理与诚实降级规则。原则：**材料不足或软件缺失时如实标注 limitations，绝不编造运行结果、评测分数、条款、文献元数据或科学数据。**

## 1. 输入降级

| 场景 | 处置 |
|---|---|
| 空输入目录 | 退出码 0，`status=insufficient_evidence`，sources 为空，errors 记录"输入目录为空" |
| `case.json` 缺失 | `case_id` 取 `unknown`，errors 记录，`status=partial` 或 `insufficient_evidence` |
| `case.json` 坏 JSON | errors 记录，继续处理其余材料；result.json 仍写合法 JSON |
| 缺 `output_contract` | 用标准 schema，`output_contract={}`，标 `contract_missing=true` |
| 文本坏编码 | 依次 utf-8-sig → utf-8 → gb18030 → latin-1 降级；仍不可读则记 degradations 并跳过 |
| 单文本文件 > 2MB | 跳过并记录，不截断造成误导 |

## 2. PDF 降级

- 首选 `pdftotext -layout`；不可用则记录 degradations 并提示用平台 `pdf-text-tool`。
- 扫描件无文本层：按题面用 `ocr-tool`；仍不可得 → `insufficient_evidence`/`partial` + errors，只覆盖已解析文本，不猜内容。
- 缺页/乱序：只对实际解析到的文本作结论，`locator` 指向已解析文本，不假设缺失页内容。

## 3. 软件/工具降级

- 脚本只依赖 Python 标准库；`pdftotext` 为可选加速，缺失不导致失败。
- 平台工具（pdf-text-tool、ocr-tool、office-renderer 等）缺失时如实写 `degradations`/`errors`，不伪造工具输出。

## 4. 输出诚实性

- `status` 只取 `ok / partial / insufficient_evidence / input_error`，如实填写：
  - `ok`：全部方向已查、无待复核项、无阻断性错误。
  - `partial`：存在待复核/未覆盖项或降级。
  - `insufficient_evidence`：无可用材料或几乎无法判读。
  - `input_error`：`case.json` 不可读且无法确定 case 身份。
- 每条 finding 的 `quote` 必须能从源文件回查；validate 校验不过则修或降级，不删除证据字段造假。
- 受限数据只标位置与类型，不复述敏感内容。

## 5. 预算降级

- 总预算 900 秒 / 64 次模型请求；前 3 分钟先落盘 partial 骨架，之后只做增量改进。
- 预算耗尽时如实写 `partial` 并在 uncertainty 列明未覆盖方向，不凑条数、不编结论。

## 6. 确定性

- 脚本候选提取、骨架、merge、validate 均为确定性过程（无随机种子、无外部网络）。
- Agent 判定遵循 audit-rules.md 的固定规则；同一输入重复运行结论应基本一致。
