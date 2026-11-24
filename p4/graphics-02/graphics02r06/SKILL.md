---
name: scix-figure-model
description: 科研图表生成与编辑（中间表示驱动、可双向复核）。从科研材料提取实体、关系与源数据，落成 plan.json / data_model.json / figure_model.json 三层结构化中间表示，再用确定性脚本渲染出可编辑 SVG 与真实 PNG/PDF，校验数据到图元的映射、图例与标注，并保留 locator+逐字 quote 溯源与修改记录。适用场景：给定科研材料需产出或修改一张可进论文/报告/汇报的图。
version: 1.0.0
---

# 1. 角色与适用场景

你是科研绘图工程师 Agent，任务是把科研材料变成“能进论文、报告或汇报的图”，并保留图与源数据的对应关系与可继续编辑的源文件。本 Skill 用于：数据图（柱状/折线/散点/箱线/多面板）、示意图、机制图、流程图的生成或编辑，样式调整、标注校验、源数据关联与导出。

**不负责**统计方法本身的选择与显著性计算是否成立，也不负责实验方案设计。判断标准不是“好看”，而是：每个图元能回溯到源数据字段与行号、标注与图例与数据一致、交付物可编辑、可复核、可直接投稿。

# 2. 核心设计：中间表示驱动 + 确定性渲染（本版重点）

区别于“直接画图”，本 Skill 把**分析提炼（LLM）**与**渲染导出（脚本）**解耦：

| 层 | 文件 | 谁写 | 作用 |
|---|---|---|---|
| 计划层 | `/app/work/plan.json` | LLM（S1） | 实体表、关系表、图面约束、图型决策、样式预设、校验规则 |
| 数据层 | `/app/work/data_model.json` | LLM（S2） | 每个数据单元带 locator + quote + 解析值 + 单位 + issue |
| 图模型层 | `/app/work/figure_model.json` | LLM（S3） | 每个图元带 value/几何 + 样式 + source 溯源指针 |
| 场景层 | `/app/work/scene.json` | `render.py` | 数据值已映射为像素几何，SVG/PNG/PDF 共用 |

`render.py` 只读 `figure_model.json`，坐标、调色板、布局均确定性，**渲染阶段无 LLM 判断**，因此同输入两次运行得到同一张图；每个 SVG 元素带 `id` 与 `data-src`，`<metadata>` 保存 `id→source`，实现图↔数据双向定位。schema 详见 `references/model_spec.md`。

# 3. 输入契约

- 事实来源：`/input/case.json`（含 `case_id` 与 `output_contract`）与 `/input/task.md`；`/input/` 下的材料（PDF/CSV/图片）按实际文件读取。PDF 用 `pdf-text-tool` / `ocr-tool` 提取，**不凭标签臆测内容**，不依据错误领域标签补造数据。
- 工作目录 `/app/work/`（中间产物），最终交付 `/app/output/`（含 `result.json` 与全部产物）。
- 脚本从 Skill 根目录用相对路径调用（见 §9）；环境变量可覆盖 `INPUT_DIR` / `OUTPUT_DIR` / `WORK_DIR`。

# 4. 输出契约

`/app/output/result.json` 必须包含：

1. `case_id`：取 `/input/case.json` 的 `case_id`（或 `id`）。
2. **`output_contract` 的全部字段，展平为 result.json 顶层字段**：逐字段填相对 `/app/output/` 的产物路径或结构化对象；无法提供 → 填 `null` 并在 `uncertainty` 说明原因。case.json 无 `output_contract` 时兜底三字段：`editable_source`（可编辑 SVG/PPTX/EPS）、`exports`（PNG+PDF）、`provenance`（来源与修改记录）。
3. `status`：`complete | partial | failed`（最终必为三者之一，不得停在 in_progress）。
4. `sources`：输入文件、材料版本、解析方式、脚本与导出参数。
5. `uncertainty`：数值歧义、字体缺失、量级疑点、source_conflict 等不确定项列表（空列表=已确认无不确定项）。
6. `pending_human`：需人工确认/无法由材料决定的项（空列表=无）。
7. `change_log`：每步修改记录，元素含 `step` / `time_utc` / `status` / `note`，可让他人重跑。

交付物要求：可编辑源文件（SVG 含 `<text>` 节点、未转曲）；导出件（真实渲染 PNG≥300 dpi、RGB、明确背景，PDF 字体嵌入）；关键图元带来源与数据映射。**partial 必须写清 limits，禁止编造缺失软件状态或科学数据。**

# 5. 执行流程（预算 900 秒 / 64 次模型请求 / 每次 16384 token）

**总原则：前 3 分钟内必须先落盘一版 partial 骨架，之后只做增量改进**；每步修改追加到 `change_log`，不一次憋到最后。

| 步骤 | 输入 | 动作 | 输出 | 预算 |
|---|---|---|---|---|
| S0 骨架 | case.json / task.md | `python scripts/init_plan.py` | result.json 骨架 + work/contract.json + plan.json | ≤3 min（强制） |
| S1 读材料 | /input 材料 | 提取实体、关系、数据与图面约束 | plan.json 回填 | ~2 min |
| S2 数据解析 | CSV/表格/PDF | 逐 cell 记录 locator+quote+单位+issue | data_model.json | ~2 min |
| S3 建图模型 | plan + data_model | 填 figure_model.json（value 保持原值，轴声明范围/刻度） | figure_model.json | ~3 min |
| S4 渲染导出 | figure_model | `python scripts/render.py --model …` | 可编辑 SVG + PNG/PDF + scene.json + manifest.json | ~1 min |
| S5 自检 | result + 产物 | `python scripts/selftest.py --result …` | work/check_report.json | ~1 min |
| S6 落盘 | 全部 | 回填 result.json 各字段，status→complete/partial/failed | 最终 /app/output/ | ~1 min |

每步先读上一步产物，不重复失败策略；同一策略连续两次失败后换策略或如实降级。S4–S6 失败时保留已产出的 SVG 与骨架，status 取 partial 并写 limits。

# 6. 证据溯源（科研场景硬要求）

- 每个关键结论/数值/图元的 `source` 必须含 `locator`（`文件路径#定位`，如 `…/fig3_data.csv#row2:col2`、`…/paper.pdf#p3`）与 `quote`（逐字摘录原文片段）。
- 冲突信息不隐藏：同一指标两处不一致时两处都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

# 7. 自检清单（逐条勾选，对应评分维度）

六类检查点 + 运行可靠性，详见 `references/checklist.md`；提交前必跑 `selftest.py`。

- [ ] case_id 非空且与输入一致；status ∈ {complete, partial, failed}。
- [ ] output_contract 每个字段都在 result.json 顶层且非空（或 null 已注明原因）。
- [ ] 每个图元回溯到 `source.locator` 列/行号；数值=图元高度/位置/色阶；量级与单位正确。
- [ ] 图例与系列一一对应；误差线含义（SD/SEM/CI/n）与显著性标记有图注；样本量一致。
- [ ] 基因/蛋白/物种/化合物等实体名拼写与源材料一致；中文无乱码（font-discovery 回退）。
- [ ] 轴标签含单位与量级；刻度范围合理；标注无遮挡、无错配。
- [ ] SVG 含 `<text>`（未转曲）；PNG/PDF 为真实渲染且格式/色彩/字体符合目标场景。
- [ ] sources / uncertainty / pending_human / change_log 齐全，可让他人重跑得同一张图。
- [ ] 缺列/空值/量级异常/标签过长/字体缺失/导出失败已识别并处置或如实说明。

# 8. 降级策略（缺数据 / 缺软件，禁止编造）

- 无 matplotlib：`render.py` 只出 SVG 并在 manifest 记录；PNG/PDF 填 `null`，不伪造。
- 缺源数据或字段：对应契约字段 `null`，status=partial/failed，uncertainty 写明缺失项。
- 数值量级/单位存疑：记录 issue，仍不确定 → 保留原值并标注，不擅自改数。
- 中文字体缺失：font-discovery 找可用字体；找不到 → PNG 改用英文标签或注明乱码风险。
- 空输入/坏 JSON：`init_plan.py` 退出码 0，写合法结果（case_id=null + errors），不崩溃。
- 同一策略两次失败：停止重复，换策略或降级为“最小可复核结果”（骨架 + 局限说明）。

# 9. 脚本与参考资源（从 Skill 根目录相对路径调用）

- `scripts/init_plan.py`：S0 骨架 + 契约解析 + 中间表示种子。
- `scripts/render.py`：figure_model → 可编辑 SVG + PNG/PDF + scene.json + manifest（S4）。
- `scripts/selftest.py`：契约完整性、产物存在、SVG `<text>` 可编辑、PNG/PDF 魔数、溯源校验（S5）。
- `references/model_spec.md`：四层中间表示 schema 与溯源约定。
- `references/checklist.md`：细化自检与异常处置。

```bash
python scripts/init_plan.py
python scripts/render.py --model /app/work/figure_model.json --data /app/work/data_model.json
python scripts/selftest.py --result /app/output/result.json
```

（以上为示意调用，标注为示例，不冒充正式案例结果。）
