# slides-01 契约与降级规则（引用规范）

## 1. 输入契约

运行时原始输入固定在 `/input/`：

- `/input/case.json`：`case_id` + `output_contract`（**字段名以它为准**，不要假设）；
- `/input/task.md`：任务说明（含受众、时长、页数上限、模板要求）；
- 材料：论文 PDF / 汇报稿 / 大纲、模板 pptx、图/表（可能在 `/input/materials/` 或平铺）。

**先发现，再假设**：`ls -R /input | head -100` 看清实际有什么；不依据文件名或领域标签补造内容。实际 PDF 由平台给定，可能没有预提取文本层，需用平台 PDF 工具（pdftotext/OCR）读原文，不能从标签猜内容。

## 2. 输出契约

产物全部写 `/app/output/`，必含 `result.json`。`result.json` 必须：

1. 原样回填 `output_contract` 的**全部字段**（缺值给 `null`/`[]`/`{}`，不改名、不删未知字段）；
2. 含 `case_id` 与 `status`（`ok | partial | insufficient_evidence | input_error`）；
3. 统一信封：`sources` / `uncertainty` / `pending_human` / `errors` / `degradations` / `change_log` / `runtime`。

若 `output_contract` 缺失，回退到规范字段（与 `task.md`「Skill 运行后的输出要求」三表对应）：

| 规范字段 | 含义 | 要求 |
|---|---|---|
| `editable_source` | 可编辑演示文件 | `{path, format, slides, template}`，真实 pptx |
| `evidence_map` | 页级证据映射 | 每页 `{page, claim, role, evidence:[{locator,quote}], figures, notes}` |
| `render_record` | 渲染与版本记录 | `{source, export_pdf, export_png, fonts, template, changes}` |
| `compliance_checks` | 合规检查记录 | 每次自检追加一条 |
| `exceptions` | 异常与缺口 | 缺失/失败/降级都要列 |

主产物：真实 `presentation.pptx` + 页数一致的 `presentation.pdf`（无 pptx 能力时给真实 SVG+PNG 并写 `exceptions`）。

## 3. 证据溯源（硬要求）

- 每个关键结论/数值/图元必须带 `locator`（图/表/章节/页码定位）+ 逐字 `quote`（`quote` 必须逐字命中材料原文）。
- 冲突信息不隐藏：同一指标两个来源不一致，两个都记录并标 `source_conflict`。
- 材料未报告/读不出 → 写 `uncertainty` 或 `pending_human`，**绝不编造**（不虚构引用、DOI、数值）。
- 示意图 ≠ 实验数据：页面注明图的类型（示意/实测），不把他人结果写成本文结果。

## 4. 模板继承（slides 题硬要求）

- 给定模板必须**真继承**母版/版式/主题（edit 起始 deck），禁止“新建空白 + 贴文本框”的伪继承。
- 优先顺序：给定模板真继承 > `python-pptx`（若预装）> `scripts/pptx_oo.py` 最小构建器（标 `degradations`）。
- 字号/配色/页脚遵守模板层级；长标题截断或换行，大表格转图或拆页，不溢出页面。

## 5. 降级策略（不编造）

| 场景 | 处置 |
|---|---|
| 缺 `case.json`/坏 JSON | 仍写 result.json，`status="input_error"`、`case_id="unknown-case"`，`errors` 记原因 |
| 图表无法提取 | 写 `exceptions`，用页面文字替代并标注“图表缺失，需人工补图” |
| 公式为图片 | 引用编号 + locator，不强行转写数值 |
| PDF 加密/无文本层 | 用 OCR/渲染工具；仍失败写 `pending_human` |
| 字体缺失 | 用平台字体发现工具；仍缺写 `render_record.fonts` 为缺失清单 + `degradations` |
| 页数超限 | 合并支撑页到备注，主线上只留必需页 |
| 无 pptx 能力 | 给真实 SVG+PNG，`exceptions` 说明可编辑源缺失 |

**没有 result.json 才是 0 分**：任何阶段崩溃前都必须先落盘一版合法 result.json。

## 6. 时间与请求预算

- 总预算：900 秒 / 64 次模型请求 / 每次 16384 tokens（含推理）。
- **前 3 分钟**必须先 `python3 -B scripts/main.py --mode init` 落盘 partial 骨架；
- **每 40 秒至少重写一次 result.json**（保持可交接）；连续思考不超过 2 分钟；
- **剩 5 分钟必须已全部落盘**；先交粗稿再改进，不一遍追求完美。
