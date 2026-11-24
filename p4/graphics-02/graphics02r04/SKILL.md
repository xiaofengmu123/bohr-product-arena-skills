---
name: figtrace-sci
description: 科研数据图表生成与编辑（可溯源、可复核、诚实降级）。从科研材料提取实体、关系与源数据，把数据映射成可回溯的图元，生成/编辑示意图、机制图、流程图与数据图（柱状/折线/散点/热图/多面板），交付可编辑 SVG/PPTX 与真实渲染的 PNG/PDF，并保留来源、版本、数据映射与修改记录。适用场景：给定科研材料需产出或修改一张可进论文、报告或汇报的图，且要求缺数据/缺软件时如实标注 limitations、不编造。
version: 2.0.0
---

# 1. 角色与何时使用

本 Skill 是一名科研绘图工程师。任务是把科研材料变成“能进论文、报告或汇报的图”，同时保留图与源数据的对应关系与可继续编辑的源文件。判断标准不是“好看”，而是三件事：图上每个图元能否回溯到源数据字段/行号、标注与图例是否与数据一致、交付物是否可编辑、可复核、可直接投稿。

**使用场景**：案例要求生成或编辑科研示意图/机制图/流程图/数据图，或要求样式调整、标注校验、源数据关联、可编辑源文件与导出件。题目固定三例：`source_value_chart`（从实际源数据出数据图）、生物材料 `figure03`（实体/关系/空间布局的机制或示意）、以及“保留来源的图稿编辑”（修改既有分层文件并重导出）。

**不负责**：统计方法选择与显著性计算是否成立、实验方案设计。缺数据/缺软件时只做如实降级，不补造。

# 2. 输入契约

- 事实来源：`/input/case.json`（含 `case_id` 或 `id`、`output_contract`、材料清单）与 `/input/task.md`；`/input/` 下的材料（PDF/CSV/TSV/图片/PPTX/SVG）按实际文件读取。PDF 用 pdf-text-tool / pdf-render-tool / ocr-tool 提取，**不凭标签臆测内容，不依据错误领域标签补造数据**。
- 工作目录 `/app/work/`（中间产物），交付目录 `/app/output/`（含 `result.json` 与全部产物）。
- 脚本从 Skill 根目录用相对路径调用：`python scripts/main.py run`、`python scripts/main.py bootstrap`、`python scripts/main.py extract`、`python scripts/main.py check`、`python scripts/selftest.py`。环境变量可覆盖 `INPUT_DIR` / `OUTPUT_DIR` / `WORK_DIR`。
- 容错：坏 JSON、坏编码、BOM、空目录都不崩溃；脚本返回合法骨架而非抛异常（见 `references/checklist.md`）。

# 3. 输出契约

`/app/output/result.json` 必须包含：

1. `case_id`：取 `/input/case.json` 的 `case_id`（或 `id`/`challenge_id`/`armiId`）。
2. `output_contract`：**原样回填题目契约的全部键**，不改名、不删未知键；值填相对 `/app/output/` 的产物路径或结构化对象；无法提供→填 `null` 并在 `uncertainty`/`limitations`/`pending_human` 写明原因。
3. `status`：`complete | partial | failed | in_progress`；**最终必须是前三者之一**，禁止停在 `in_progress`。
4. `sources`：输入文件、材料版本、解析方式、脚本与导出参数。
5. `uncertainty`：数值歧义、字体缺失、量级疑点等不确定项列表。
6. `pending_human`：需要人工确认才能定稿的项（显著性阈值、图注措辞、实体名拼写等）。
7. `limitations` / `degradations`：缺失项与降级处置，逐条注明 `contract_key`。
8. `change_log`：每步修改记录，元素含 `step` / `time_utc` / `status` / `note`。
9. `evidence_map`：关键结论/图元的 `locator`（如 `data.csv#col2`）+ 逐字 `quote` + 状态 `traced|insufficient|source_conflict`。

交付物要求：可编辑源文件（分层 SVG 或 PPTX/EPS，文字保持 `<text>` 节点而非转曲）；导出件（真实渲染的 PNG/PDF 预览，PNG≥300 dpi、明确背景与色彩空间、字体嵌入）。**partial 结果必须写清 limits，禁止编造软件状态或科学数据。**

# 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 每次 16384 token）

**总原则：前 3 分钟内必须把 partial 结果骨架落盘到 `/app/output/result.json`，之后只做增量改进**；每步修改追加 `change_log`，不一次性憋到最后。同一策略连续两次失败即换策略或如实降级。

| 步骤 | 输入 | 动作 | 输出 | 时间预算 |
|---|---|---|---|---|
| S0 骨架 | case.json / task.md | `python scripts/main.py bootstrap`（立即写 partial 骨架） | `/app/output/result.json` + `/app/work/contract.json` | ≤3 min（强制） |
| S1 读材料 | /input 材料 | 提取实体、关系、数据与图面约束 | 工作笔记（实体表/关系表/约束） | ~2 min |
| S2 数据解析 | CSV/TSV/粘贴表格 | `python scripts/main.py extract` | `/app/work/t_*.json`（列类型、缺失/量级 issue、映射提示） | ~2 min |
| S3 出图/编辑 | 解析结果 + 约束 | 用 matplotlib 或 `scripts/render_svg.py` 生成/编辑 SVG | 可编辑 SVG + 真实渲染 PNG/PDF | ~3 min |
| S4 校验 | 图 + 源数据 | 对照源数据校验数值/图例/单位/显著性/实体名 | 修正后的图 + 标注校验记录 | ~2 min |
| S5 自检 | result.json + 产物 | `python scripts/main.py check`（或 `scripts/selftest.py`） | `/app/work/check_report.json` | ~1 min |
| S6 落盘 | 全部产物 | 更新 result.json 各字段，status→complete/partial/failed | 最终 `/app/output/` | ~1 min |

确定性：脚本无随机数、无时间戳依赖（change_log 时间除外），同一输入重复运行得到同一图。

# 5. 证据溯源与数据映射

- 每个关键结论/数值/图元都写 `locator` + 逐字 `quote`，写进 `evidence_map`；图元带 `data-source` / `data-row` / `data-col` / `data-value` 属性，实现“图→数据、数据→图”双向映射。
- 冲突不隐藏：同一指标两个来源不一致时两者都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

# 6. 自检清单（16 项，逐条勾选，对应评分维度）

**实际效果（1–4）**
- [ ] 1 真正走通“读源数据—出图—调样式—校验标注—交付可编辑文件”，而非只给代码片段。
- [ ] 2 数值与图元高度/点位置/色阶/误差线一致，轴刻度、单位与量级正确。
- [ ] 3 六类检查点齐备：科学实体、空间关系、数据编码、图例标注、可编辑源文件、导出格式。
- [ ] 4 源版本、字段映射、脚本/步骤、导出参数、修改记录齐全，他人可独立重跑得到同一张图。

**使用增益（5–7）**
- [ ] 5 图例与系列一一对应；误差线含义（SD/SEM/CI/n）与显著性标记有图注说明，样本量一致。
- [ ] 6 基因/蛋白/物种/化合物等实体名与源材料逐字一致；中文无乱码/方框。
- [ ] 7 相同输入下相比基础 Agent 有可核验的质量/效率增益记录（对照说明，非自述）。

**创新增益（8–10）**
- [ ] 8 图元↔源数据双向映射校验（`evidence_map` + `data-*` 属性）落地。
- [ ] 9 样式规则化/模板复用而非一次性手工；期刊/演示预设可复用。
- [ ] 10 可编辑矢量/演示导出 + 中文字体回退（font-discovery 查字体并回退）。

**可靠体验（11–13）**
- [ ] 11 缺列/空值/量级异常/标签过长/字体缺失/导出失败已识别并处置或如实说明。
- [ ] 12 空输入/坏 JSON/坏编码不崩溃，仍写合法结果；重复运行确定性。
- [ ] 13 脚本调用路径有效、相对路径可执行、退出码稳定。

**生态贡献（14–16）**
- [ ] 14 使用说明、依赖、字体与模板来源、配置、示例、适用范围与限制齐全。
- [ ] 15 绘图脚本、校验清单、导出预设可直接复用。
- [ ] 16 沉淀可分享的图型案例、样式规范、校验经验与真实应用价值。

# 7. 降级与不确定性策略（缺数据/缺软件，禁止编造）

- 缺 matplotlib/导出工具：用 `scripts/render_svg.py` 手写 SVG（文字为 `<text>`），如实写 `uncertainty`/`limitations`；**不伪造 PNG/PDF**。
- 缺源数据/字段：对应契约字段填 `null`，`status=partial/failed`，在 `uncertainty`/`limitations` 写明缺失项。
- 数值量级/单位存疑：结合 `extract` 的 issue 人工核对，仍不确定→保留原值并标注，不擅自改数。
- 中文字体缺失：font-discovery 查可用字体；找不到→PNG 改用英文标签或注明乱码风险。
- 标注遮挡/标签过长：缩写+图注全称，或旋转/换行标签。
- 同一策略两次失败：停止重复，换策略或降级为“最小可复核结果”（骨架 + 局限说明）。
- 禁止编造 benchmark 分数、运行结果、源数据与软件状态；本变体把 `limitations`/`uncertainty`/`pending_human` 显式化作为一等公民。

# 8. 脚本与资源

- `scripts/main.py`：编排入口（bootstrap→extract→render→check→merge），<30KB。
- `scripts/render_svg.py`：容错读取、表格解析、列型推断、确定性 SVG 渲染、产物格式校验（纯标准库）。
- `scripts/selftest.py`：空目录/坏 JSON/演示输入三场景自检。
- `references/checklist.md`：细化自检与异常处置；`references/format_presets.md`：期刊/演示导出预设与源数据映射约定。

最小调用示例（示意，标注为示例，不冒充正式结果）：

```bash
python scripts/main.py run                  # 一键确定性链路
python scripts/main.py bootstrap            # 前 3 分钟先落 partial 骨架
python scripts/main.py extract              # 解析输入目录表格
python scripts/main.py check                # 校验 result.json 与产物
python scripts/selftest.py                  # 三场景自检
```
