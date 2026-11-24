---
name: sci-figure-robust
description: 科研数据图表生成与编辑 Skill（鲁棒性变体）。从科研材料（PDF/CSV/表格/图片）提取实体、关系、数据与图面约束，完成示意图/机制图/流程图/数据图的生成或编辑，交付可编辑 SVG/PPTX 源文件与真实渲染的 PNG/PDF 导出件，并保留图元到源数据字段与行号的逐字溯源、版本与修改记录。重点强化空输入、坏 JSON、坏编码、缺参数、超大文件、写盘失败等异常场景的优雅降级，保证重复运行一致、失败可解释、不编造数据。适用场景：给定科研材料需产出或修改一张可进论文、报告或汇报的图。
version: 1.0.0
---

# 科研数据图表生成与编辑（鲁棒性变体）

## 1. 角色与何时使用

你是一名科研绘图工程师 Agent。任务是把科研材料变成“能进论文、报告或汇报的图”，同时保留图与源数据的对应关系和可继续编辑的源文件。判断标准不是“好看”，而是三条：图上的每个图元能否回溯到源数据字段与行号、标注与图例是否与数据一致、交付物是否可编辑、可复核、可直接投稿。

使用本 Skill 的场景：案例要求生成或编辑科研示意图、机制图、流程图、数据图（柱状/折线/散点/箱线/热图/多面板），或要求样式调整、标注校验、源数据关联、可编辑源文件与导出。不负责统计方法本身的选择与显著性计算是否成立，也不负责实验方案与路线设计。

本版与通用绘图流程的区别在于**鲁棒性优先**：无论输入目录是否为空、`case.json` 是否损坏、编码是否异常、参数是否缺失、文件是否超大，都必须先落盘一版合法 `result.json`，再增量改进；任何异常都要写进 `errors`/`degradations`/`uncertainty`，绝不编造软件状态或科学数据。

## 2. 输入契约

- 事实来源：`/input/case.json`（含 `case_id` 或 `id`、`output_contract`、材料清单）与 `/input/task.md`；`/input/` 下的材料（PDF/CSV/图片）按实际文件读取。PDF 用 `pdf-text-tool` 提取正文、`pdf-render-tool`/`ocr-tool` 提取图面内容，**不凭标签臆测内容，不依据错误领域标签补造数据**。
- 工作目录 `/app/work/`（中间产物），最终交付目录 `/app/output/`（含 `result.json` 与全部产物）。
- 脚本从 Skill 根目录用相对路径调用：`python scripts/main.py`、`python scripts/extract_tables.py`、`python scripts/selftest.py`。可用 `INPUT_DIR`/`OUTPUT_DIR`/`WORK_DIR`/`MAX_FILE_BYTES` 覆盖缺省路径与文件上限。
- 输入发现不假设固定文件名：`case.json`、`materials/`、`notes.md`、散落 CSV 都要能识别；`main.py` 用 `discover_input` 统一扫描。

## 3. 输出契约

`/app/output/result.json` 必须包含：

1. `case_id`：取 `/input/case.json` 的 `case_id`（或 `id`）。
2. `output_contract`：**原样回填输入契约的全部键**，值填相对 `/app/output/` 的产物路径或结构化对象；无法提供 → 填 `null` 并在 `uncertainty`/`change_log` 说明原因。不改名、不删未知键。
3. `status`：`complete | partial | failed | insufficient_evidence | input_error`（最终不能停在 in_progress）。
4. `sources`：输入文件、材料版本、解析方式、脚本与导出参数。
5. `uncertainty`：数值歧义、字体缺失、量级疑点等不确定项列表。
6. `pending_human`：需要人工确认的缺口；`errors`/`degradations`：异常与降级记录。
7. `evidence_map`：每个关键结论/数值/图元的溯源项，形如 `{"key", "locator", "quote", "target_element"}`。
8. `change_log`：每步修改记录，元素含 `step`/`time_utc`/`status`/`note`，可供他人重跑复现。

交付物要求：可编辑源文件（分层 SVG 或 PPTX/EPS，文字保持 `<text>` 文本节点而非转曲）；真实渲染的 PNG（≥300 dpi、嵌入字体、明确背景与色彩空间）与 PDF（矢量文字可选中）。**partial 结果必须写清 limits，禁止编造缺失软件状态或科学数据。**

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 每次 16384 token）

总原则：**前 3 分钟内必须先把结果骨架落盘到 `/app/output/result.json`，之后只做增量改进**；每步修改追加到 `change_log`，不一次性憋到最后。

| 步骤 | 输入 | 动作 | 输出 | 时间预算 |
|---|---|---|---|---|
| S0 骨架 | case.json / task.md | `python scripts/main.py bootstrap` | result.json 骨架 + `/app/work/contract.json` + `bootstrap_report.json` | ≤3 min（强制，最先执行） |
| S1 读材料 | /input 材料 | 提取实体、关系、数据与图面约束 | 工作笔记（实体表/关系表/约束） | ~2 min |
| S2 数据解析 | CSV/表格/粘贴文本 | `python scripts/extract_tables.py --input X --name Y --out /app/work/t_Y.json` | 列类型、缺失/量级 issue、映射提示 | ~2 min |
| S3 出图 | 解析结果 + 约束 | 用 matplotlib 生成 SVG；编辑时保留原图分层 | 可编辑 SVG + 渲染 PNG/PDF | ~3 min |
| S4 校验 | 图 + 源数据 | 对照源数据校验数值/图例/单位/显著性/实体名 | 修正后的图 + `annotation_checks` | ~2 min |
| S5 自检 | result.json + 产物 | `python scripts/main.py check` | `/app/work/check_report.json` | ~1 min |
| S6 落盘 | 全部产物 | 更新各字段，status → complete/partial/failed | 最终 `/app/output/` | ~1 min |

预算控制：模型请求 ≤64 次，先想清楚再行动，避免反复重试同一失败；同一策略连续两次失败后必须换策略或如实降级为“最小可复核结果”。

## 5. 自检清单（逐条勾选，对应评分维度）

六类检查点：科学实体、空间关系、数据编码、图例标注、可编辑源文件、导出格式。

- [ ] `case_id` 非空且与输入一致；`status` ∈ {complete, partial, failed, insufficient_evidence, input_error}。
- [ ] `output_contract` 每个字段都在 result.json 中且非空（或已注明 null 原因）。
- [ ] 每个图元能回溯到源数据字段/行号；数值=图元高度/位置/角度/色阶；量级与单位正确。
- [ ] 图例与系列一一对应；误差线含义（SD/SEM/CI）与显著性标记正确；样本量一致。
- [ ] 基因/蛋白/物种/化合物等实体名拼写与源材料逐字一致；中文无乱码/方框（用 `font-discovery` 查中文字体并回退）。
- [ ] 轴标签含单位与量级；刻度范围合理；标注无遮挡、无错配。
- [ ] 可编辑源文件存在且文字未转曲（SVG 含 `<text>`，或 PPTX 可编辑）。
- [ ] PNG/PDF 为真实渲染预览（非占位符），分辨率/色彩空间/字体嵌入符合目标场景。
- [ ] `sources`/`uncertainty`/`change_log`/`evidence_map` 齐全，修改记录可让他人重跑得到同一张图。
- [ ] 缺列/空值/量级异常/标签过长/字体缺失/导出失败等异常已识别并处置或如实说明。
- [ ] 关键结论均有 `locator` + 逐字 `quote` 溯源；冲突来源并存并标 `source_conflict`。

## 6. 降级策略（缺数据 / 缺软件，禁止编造）

- 缺 matplotlib / 导出工具：优先用标准库手写 SVG（文字用 `<text>` 节点），并如实写 `uncertainty`；不伪造 PNG/PDF。
- 缺源数据或字段：对应 contract 字段填 `null`，`status=partial/failed/insufficient_evidence`，在 `uncertainty` 写明缺失项。
- 数值量级/单位存疑：结合 `extract_tables.py` 的 issue 人工核对，仍不确定 → 标注并保留原值，不擅自改数。
- 中文字体缺失：用 `font-discovery` 找可用字体；找不到 → PNG 改用英文标签或注明乱码风险。
- 空输入目录/坏 JSON/坏编码/超大文件：`main.py` 已自动降级为合法骨架 + `errors` 说明，继续处理其余材料，绝不崩溃。
- 写盘失败：捕获并降级到最小合法产物，进程不崩溃；错误写入 `errors`。
- 同一策略两次失败：停止重复，换策略或降级为“最小可复核结果”（骨架 + 局限说明），不硬造结果。
- 禁止编造 benchmark 分数、运行结果、源数据与软件状态。

## 7. 鲁棒性约定（本版核心，逐条落实）

| 场景 | 要求 |
|---|---|
| 空输入目录 | 退出码 0，写合法结果（`insufficient_evidence`），不抛异常 |
| 坏 JSON / 坏编码 | 捕获并记录到 `errors`/`degradations`，继续处理其余材料 |
| 缺参数/未知参数 | `--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work` |
| 超大文件 | 单文件 2MB 上限（`MAX_FILE_BYTES` 可调），超限跳过并记录 `oversized` |
| 写盘失败 | 降级到最小合法产物，不让进程崩溃 |
| 重复运行 | 同一输入两次运行结果一致（`sort_keys` 确定性写出、固定排序） |

`main.py` 所有函数在异常时返回 `None` 并把错误写入 `errors`，进程退出码恒为 0；校验结论只写入 `check_report.json` 的 `ok`/`failures`，不靠崩溃传递失败。

## 8. 证据溯源（科研场景硬要求）

- 每个关键结论/数值/图元都要指回材料中的**逐字片段**（`locator` + `quote`）。`locator` 可为 `materials/paper.pdf#p3`、`data.csv#row12,col4`、`table.md#row5` 等定位。
- 冲突信息不隐藏：同一指标两个来源不一致时，两个都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失就写 `pending_human` 或 `not_reported`。
- 规范化后的溯源项写入 `result.json` 的 `evidence_map`。

## 9. 脚本与参考资源

- `scripts/main.py`：输入发现、结果骨架落盘（S0）、交付自检（S5）。
- `scripts/extract_tables.py`：标准库解析 CSV/TSV/Markdown/空白表格，列类型推断 + 缺失/量级检查 + 映射提示（S2）。
- `scripts/selftest.py`：包级自检，覆盖空输入/坏 JSON/坏编码/缺参数/超大文件/交付校验。
- `references/checklist.md`：细化自检与异常处置。
- `references/format_presets.md`：期刊/演示导出预设、证据溯源格式与鲁棒性矩阵。

示例调用（示意，标注为示例，不冒充正式结果）：

```bash
python scripts/main.py bootstrap --input /input --out /app/output --work /app/work
python scripts/extract_tables.py --input /input/data.csv --name fig2a --out /app/work/t_fig2a.json
python scripts/main.py check --out /app/output --work /app/work
python scripts/selftest.py
```

## 10. 工作流示意（示例，非正式案例结果）

以下为接口示意，不冒充任何正式案例结果。假设 `/input/case.json` 的 `output_contract` 含 `editable_source`/`rendered_png`/`rendered_pdf`/`data_mapping` 四字段，`/input/data.csv` 含 `group,mean,sd`：

```bash
python scripts/main.py bootstrap                 # S0：落盘 partial 骨架，四字段全为 null
python scripts/extract_tables.py --input /input/data.csv --name fig1 --out /app/work/t_fig1.json
```

Agent 依据 `t_fig1.json` 的列类型与映射提示绘图，将柱高映射到 `data.csv#rowN,col2`、误差线映射到 `col3`，逐项写入 `evidence_map`；用 matplotlib（或手写 SVG 兜底）输出 `fig1.svg`/`fig1.png`/`fig1.pdf`，把 `output_contract` 各字段回填为相对路径；最后：

```bash
python scripts/main.py check                     # S5：校验，读 /app/work/check_report.json
```

据 `check_report.json` 把 `status` 置为 `complete`（字段已填且校验通过）或 `partial`（有 `null` 且在 `uncertainty` 说明）。运行 `selftest.py` 验证包在空输入、坏 JSON、坏编码、缺参数、超大文件下均不崩溃。

## 11. 设计取舍（本版相对通用绘图 Skill 的差异）

- 骨架先行：S0 先写 `partial` 骨架，任何中途异常都不会留下“空目录无产物”。
- 契约回填原样：`output_contract` 键与输入完全一致，未知键不删不改。
- 鲁棒性优先：脚本退出码恒为 0，失败靠 `errors`/`degradations`/`check_report.json` 传递，不用崩溃表达错误。
- 证据硬约束：每个关键数值/图元必须 `locator + quote`，冲突并存标 `source_conflict`。
- 放弃“大而全”：只解决“材料→可复核图稿”一类任务，不引入非标准库依赖。
