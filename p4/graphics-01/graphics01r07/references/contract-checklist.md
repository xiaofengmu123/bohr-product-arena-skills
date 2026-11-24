# result.json 契约与降级矩阵（sci-schematic-editor）

`result.json` 是每个案例的结构化交付物。字段定义、状态语义、证据溯源要求、降级规则在此统一，供 LLM 填表与 `main.py validate` 校验。

## 1. 字段定义

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `case_id` | string | 是 | 从 `case.json` 提取；缺失填 `unknown` |
| `status` | string | 是 | `complete` / `partial` / `failed` |
| `editable_source_files` | array | 是 | 分层 SVG 源文件：`{path, kind, layers, editable_text, note}`；图表另附数据文件 |
| `export_files` | array | 是 | 导出件：`{path, format, width_px, height_px, dpi, color_space, font_check, note}` |
| `provenance` | array | 是 | 图元证据：`{element, source_ref, quote, mapping, version, confirmed}` |
| `sources` | array | 是 | 材料定位：`{ref, path, locator, kind, ocr, used_for, note}` |
| `uncertainty` | array | 是 | 不确定项：`{item, reason, impact, mitigation}` |
| `pending_human` | array | 是 | 待人工复核：`{item, why, suggested_action}` |
| `errors` | array | 是 | 输入/运行错误：`{item, reason, impact, mitigation}` |
| `degradations` | array | 是 | 降级记录：`{item, reason, impact, mitigation}` |
| `change_log` | array | 是 | `{time, action, detail}`，如实记录修改与返工 |
| `runtime` | object | 是 | `{started_iso, ended_iso, elapsed_s, model_requests_used}` |

**output_contract 回填**：`scaffold` 会把 `case.json` 里 `output_contract` 声明的**全部键**原样补进 result（未知键给 `null`，有 `default` 用默认值），不重命名、不删除；标准字段覆盖同名冲突键。

**空数组不得静默省略**：任一数组为空须在对应条目或 `uncertainty` 中写原因。

## 2. 状态语义

| status | 何时使用 |
|-|-|
| `complete` | 全部交付且 `validate` 无 error |
| `partial` | 部分交付 / 材料不足 / 有 limits；limits 写入 uncertainty + change_log |
| `failed` | 输入不可读 / 无法产出；原因写入 errors |

材料不足用 `partial` + `uncertainty`；输入不可读用 `failed` + `errors`。**不编造**基因名、浓度、时间点、单位、DOI、评测分数。

## 3. 证据溯源（硬要求）

每个**已确认**的关键结论/数值/图元在 `provenance` 中必须带：

- `source_ref`：`sources[].ref` 的引用（材料定位键）
- `quote`：材料中的**逐字原文**片段
- `locator`（放在 sources）：页码/节/行，如 `paper.pdf#p3`、`data.csv#L12`

冲突不隐藏：同一指标两处不一致，两处都记，并标 `source_conflict`。缺失写 `pending_human` 或 `not_reported`。OCR 来源须在 `sources.ocr=true` 并标注「OCR 识别，需人工复核」。

## 4. 健壮性降级矩阵

| 场景 | 处置 | 出口 |
|-|-|-|
| 空输入目录 | 不崩溃，`partial` + uncertainty「materials」 | 退出码 0 |
| 坏 JSON / 坏编码 | utf-8-sig→utf-8→latin-1→replace 回退；坏 JSON 尽力打捞；记录 errors/degradations，继续处理其余材料 | 退出码 0 |
| 缺参数 / 未知参数 | 默认 `/input`、`/app/output`、`/app/work`；未知参数忽略 | — |
| 单文件 >2MB | 截断读头或跳过，sources/degradations 记录 | 退出码 0 |
| 输入总量 >40MB | 仅提示，大文件读头 | 退出码 0 |
| 缺 PDF 文本层 | pdftotext/pdftoppm/OCR，sources 标 OCR | — |
| 缺字体 | font-discovery 找替代并记录替换；无法嵌入 `font_check="none"` + uncertainty | — |
| 渲染/导出失败 | 保留 SVG，export_files 记 failed+原因，`partial`，不伪造 PNG | — |
| 写盘失败 | 降级为最小合法产物（stdout 摘要），不让进程崩溃 | 退出码 0 |

**重复运行确定性**：无随机数、实体/关系按 id 稳定排序、自动布局算法确定，同一输入两次运行结果一致。

## 5. 评分维度对照自检

- 实际效果：实体/关系/方向/层级与来源一致；图例配色与数据表逐项一致；provenance 可独立复核（locator+quote）。
- 使用增益：改 `figure_spec.json` 重跑 `main.py build` 可重绘；SVG 文本可编辑；change_log 如实记录人工修改量与返工。
- 创新增益：文字→实体关系清单方法可说明；数据表驱动图例；`main.py validate` 真实运行。
- 可靠体验：空输入/坏 JSON/坏编码/缺参数/超大文件不崩溃；异常可识别可处置。
- 生态贡献：模板/配色/图例规范/导出预设可复用；不预写正式答案。

## 6. 校验器检查项（main.py validate）

契约字段存在性、`case_id`、`status` 合法值、complete 状态下主产物非空、confirmed provenance 的 element/source_ref/quote 非空、SVG 可解析且含 `<g>`/`<text>`、PNG 魔数 `89 50 4E 47`、PDF 魔数 `%PDF-`、导出尺寸/dpi 元信息、`font_check` 取值。报告写入 `work/validation_report.json`，只报告不改写结果。
