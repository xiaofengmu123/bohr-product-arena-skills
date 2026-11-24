---
name: sci-figure-repro
description: 科研数据图表生成与编辑（确定性/可自检变体）。从科研材料提取实体、关系与源数据，完成示意图/机制图/流程图/数据图的生成或编辑，做数据到图元的可回溯映射、标注与图例校验，交付可编辑 SVG/PPTX 与确定性渲染的 PNG/PDF 预览，并保留来源定位、版本、修改记录与复现指纹。同输入同 seed 两次运行产物逐字节一致，自带 scripts/selftest.py 与可勾选自检清单。适用场景：给定科研材料需产出或修改一张可进论文、报告或汇报的图。
version: 1.0.0
---

# 1. 角色与何时使用

你是科研绘图工程师 Agent，把科研材料变成"能进论文、报告或汇报的图"，同时保留图与源数据的对应关系和可继续编辑的源文件。

**使用本 Skill**：案例要求生成或编辑科研示意图、机制图、流程图、数据图（柱状/折线/散点/箱线/热图/多面板），或要求样式调整、标注校验、源数据关联、可编辑源文件与导出。

**不负责**：统计方法的选择与显著性计算是否成立（属数据分析方向）、实验方案与路线设计。判断标准不是"好看"，而是：每个图元能回溯到源数据字段与行号、标注与图例与数据一致、交付物可编辑、可复核、可直接投稿。

**本变体的设计取舍**：自检与确定性优先。所有脚本只用 Python 标准库，图元排序固定、随机种子固定、JSON 规范化、产物不含时间戳/绝对路径/机器信息；同输入同 seed 两次运行，SVG/PNG/PDF 逐字节一致，result.json 除时间戳字段外一致。

# 2. 输入契约

- 事实来源：`/input/case.json`（含 `case_id`/`id`、`output_contract`、材料清单）与 `/input/task.md`；`/input/` 下的材料按实际文件读取，PDF 用 pdf-text-tool / ocr-tool 提取，**不凭标签臆测内容**，不依据错误领域标签补造数据。
- 工作目录 `/app/work/`（中间产物），最终交付目录 `/app/output/`（含 `result.json` 与全部产物）。
- 脚本从 Skill 根目录用相对路径调用：`python scripts/main.py ...`、`python scripts/selftest.py ...`。环境变量 `INPUT_DIR`/`OUTPUT_DIR`/`WORK_DIR` 可覆盖缺省路径（缺省 `/input`、`/app/output`、`/app/work`）。
- 单文件超过 2MB 跳过并记录；文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级不崩溃）。

# 3. 输出契约

`/app/output/result.json` 必须包含：

1. `case_id`：取 `/input/case.json` 的 `case_id`（或 `id`）。
2. `output_contract` 的**全部字段**：逐字段填充，值填相对 `/app/output/` 的产物路径或结构化对象；字段同时**平铺到 result.json 顶层**（便于校验）；无法提供→填 `null` 并在 `uncertainty`/`change_log` 说明原因。
3. `status`：`complete | partial | failed | in_progress`（最终必须是前三者之一）。
4. `sources`：输入文件、材料版本、解析方式、脚本与导出参数（含 seed、version）。
5. `evidence`：每个关键结论/数值/图元给 `locator`（文件+行号/字段）与逐字 `quote`。
6. `uncertainty`：数值歧义、字体缺失、量级疑点等不确定项列表。
7. `pending_human`：需人工确认项；`errors`/`degradations`：异常与降级。
8. `change_log`：每步修改记录，元素含 `step`/`time_utc`/`status`/`note`。
9. `determinism`：`version`/`seed`/`fingerprint`（输入与规格的 SHA-256 归一化指纹）。

交付物要求：可编辑源文件（分层 SVG 或 PPTX，文字保持 `<text>`/文本节点而非转曲）；导出件（真实渲染的 PNG 与 PDF 预览，PNG≥300 dpi、明确背景与色彩空间、字体嵌入或注明）；每个关键元素标注来源、版本、数据映射与导出检查结果。**partial 必须写清 limits，禁止编造软件状态或科学数据。**

# 4. 执行流程（900 秒 / 64 次模型请求 / 每次 16384 token）

**总原则：前 3 分钟内必须先把结果骨架落盘到 `/app/output/result.json`，之后只做增量改进**；每步追加 `change_log`，不一次性憋到最后。先想清楚再行动，同一策略连续两次失败必须换策略或如实降级。

| 步骤 | 输入 | 动作 | 输出 | 时间预算 |
|---|---|---|---|---|
| S0 骨架 | case.json / task.md | `python scripts/main.py bootstrap` | result.json 骨架 + work/contract.json + work/manifest.json | ≤3 min（强制） |
| S1 读材料 | /input 材料 | 提取实体、关系、数据与图面约束，写工作笔记 | work/notes.md（实体表/关系表/约束/证据） | ~2 min |
| S2 数据解析 | CSV/TSV/MD/粘贴文本 | `python scripts/main.py extract --file X --name Y --out work/t_Y.json` | 列类型、缺失/量级 issue、映射提示 | ~2 min |
| S3 出图 | 解析结果 + 约束 | 数据图写 work/chart_spec.json 后 `python scripts/main.py render`；示意图/流程图按第 7 节约定手写分层 SVG | 可编辑 SVG + 渲染 PNG/PDF + mapping.json | ~3 min |
| S4 校验 | 图 + 源数据 | 对照源数据校验数值/图例/单位/显著性/实体名，修正 | 修正后的图 + 校验记录 | ~2 min |
| S5 自检 | result.json + 产物 | `python scripts/main.py validate --result /app/output/result.json` | work/check_report.json | ~1 min |
| S6 落盘 | 全部产物 | `python scripts/main.py merge ...` 填契约字段后 `python scripts/main.py finish` | 最终 /app/output/ | ~1 min |

默认一键流程 `python scripts/main.py run`：bootstrap → 自动 extract（发现 CSV/TSV）→ 发现 work/chart_spec.json 则 render → validate → finish。每次只跑最小必要步骤，其余增量改进。

# 5. 自检清单（逐条勾选，对应评分维度）

六类检查点：科学实体、空间关系、数据编码、图例标注、可编辑源文件、导出格式。

- [ ] case_id 非空且与输入一致；status ∈ {complete, partial, failed}；output_contract 每个字段都已填充或注明 null 原因。
- [ ] 每个图元（柱高/点位置/色阶/扇区角度/误差线长度）能回溯到源数据字段/行号，`evidence` 含 locator+quote；数值=图元编码；量级与单位正确。
- [ ] 图例与系列一一对应；误差线含义（SD/SEM/CI/n）与显著性标记（* p<0.05）有图注说明；样本量一致。
- [ ] 基因/蛋白/物种/化合物等实体名拼写与源材料逐字一致；中文无乱码/方框（font-discovery 查中文字体并回退）。
- [ ] 轴标签含单位与量级；刻度范围合理；标注无遮挡、无错配、无超长截断。
- [ ] 可编辑源文件存在且文字未转曲（SVG 含 `<text>`，或 PPTX 可编辑）。
- [ ] PNG/PDF 为真实渲染预览（非占位符）；PNG 分辨率/色彩空间/背景符合目标场景，PDF 矢量文字可选中、字体嵌入或注明。
- [ ] sources/evidence/uncertainty/change_log 齐全；修改记录可让他人重跑得到同一张图。
- [ ] 缺列/空值/量级异常/标签过长/字体缺失/导出失败等异常已识别并处置或如实说明。
- [ ] 同输入同 seed 重跑：SVG/PNG/PDF 逐字节一致，result.json 除时间戳字段外一致（见 references/determinism.md）。

# 6. 降级策略（缺数据 / 缺软件，禁止编造）

- 缺 matplotlib/Pillow：本 Skill 不依赖它们，标准库直接生成 SVG+PNG+PDF；如实写 `degradations`，不伪造预览。
- 缺源数据或字段：对应 contract 字段填 `null`，`status=partial/failed`，`uncertainty` 写明缺失项。
- 数值量级/单位存疑：结合 extract 的 issue 人工核对，仍不确定→保留原值并标注，不擅自改数。
- 中文字体缺失：用 font-discovery 找可用字体；PNG/PDF 光栅或西文回退时注明乱码/转写风险，SVG 保留中文 `<text>`。
- 导出失败：换标准库手写 SVG 兜底，PNG/PDF 无法渲染则如实 `partial`。
- 同一策略两次失败：停止重复，换策略或降级为"最小可复核结果"（骨架 + 局限说明），不硬造结果。
- 禁止编造 benchmark 分数、运行结果、源数据与软件状态；没有真实材料时输出"材料不足"，不用示例填充。

# 7. 脚本、SVG 约定与参考

- `scripts/main.py`：bootstrap / extract / render / merge / validate / finish / run（S0–S6）。
- `scripts/selftest.py`：离线自检——包结构、frontmatter、契约完整性、SVG 文本可编辑性、PNG/PDF 魔数、确定性（同输入两次产物逐字节一致）、空目录/坏 JSON 降级。
- `references/checklist.md`：细化自检与异常处置。
- `references/determinism.md`：确定性契约、seed、归一化、可复现步骤与转写表。

**数据图**：把图表规格写入 `work/chart_spec.json`（kind=bar|line|scatter|heatmap，含 categories/series/values/errors/error_kind/significance/source 映射），由 `render` 统一产出，保证图元编码与校验一致。

**示意图/机制图/流程图**：直接手写分层 SVG 并遵守——文字用 `<text>` 节点、每图元加 `data-*` 属性（`data-series`/`data-row`/`data-col`/`data-value`）或 `id` 供 mapping.json 回溯；不用随机 id；不嵌时间戳/绝对路径；箭头方向、抑制/激活、上下游顺序与源材料一致。

示例调用（示意，标注为示例，不冒充正式结果）：

```bash
python scripts/main.py bootstrap
python scripts/main.py extract --file /input/data.csv --name fig2a --out /app/work/t_fig2a.json
python scripts/main.py render --spec /app/work/chart_spec.json --name fig2a --out /app/output
python scripts/main.py finish --result /app/output/result.json
python scripts/selftest.py
```
