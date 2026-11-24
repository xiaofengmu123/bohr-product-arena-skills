# 降级策略与局限（limitations）

本文件规定材料、软件或工具不可得时的处置方式。原则：**如实标注 limitations，绝不编造**。

## 1. 材料解析失败

- PDF 无文本层：依次尝试
  1. `scripts/extract.py` 自动调用的 `pdftotext`（已内置于 main.py 流程）；
  2. 平台 `pdf-text-tool`（解析到 `/app/work/parsed/<原名>.txt` 后重跑 main.py）；
  3. 平台 `ocr-tool`。
  仍不可得 → `status ∈ {insufficient_evidence, partial}`，把失败写入 `degradations`，只覆盖已解析文本，不猜内容。
- 非 PDF 文本坏编码：脚本按 utf-8-sig → utf-8 → gb18030 → latin-1 顺序降级，仍失败则记录 `errors`，跳过该文件。
- 坏 JSON（case.json 或材料）：记录 `errors`，继续处理其余材料；result.json 仍写合法。
- 单文件 >2MB：跳过并记入 `degradations`（脚本硬上限，避免超预算）。

## 2. 契约缺失或异常

- 缺 `output_contract`：`output_contract` 写 `{}`，`contract_missing=true`，用默认 schema 继续。
- `case_id` 缺失：用 case.json 文件名，并在 `uncertainty` 记录。

## 3. 软件/工具缺失

- `pdftotext` 不在 PATH：记入 `degradations`，请 Agent 用平台 pdf-text-tool 补解析后重跑。
- 缺少 Office/LaTeX 渲染工具：只交付 md/json（文本可复核），在 limitations 注明未产出预览，不伪造渲染结果。
- 不伪造运行结果、评测分数、软件状态、伦理批件号、DOI。

## 4. 空输入与异常

- 空输入目录：退出码 0，`status=insufficient_evidence`，sources 为空，errors 记录"输入为空"。
- 缺参数/未知参数：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 写盘失败：脚本用临时文件 + `os.replace` 原子写；失败时保留上一版合法产物，不覆盖成半截文件。

## 5. 受限数据

- 只标位置与类型（如"第 3 段出现可识别邮箱/身份证号"），**不复述、不扩散**敏感内容。
- 拿不准是否可披露 → `pending_human`，建议人工确认。

## 6. 预算与确定性

- 总预算 900 秒 / 64 次模型请求；前 3 分钟必须落盘 partial 骨架，之后只做增量改进。
- 脚本无随机、固定排序与布局；同一输入重跑结论基本一致。
- 预算不足时如实写 `partial` 并列出未覆盖维度，不把半成品写成 `ok`。
