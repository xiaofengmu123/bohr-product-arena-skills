---
name: scix-graphics
description: 科研数据图表生成与编辑。从案例材料（PDF/CSV/图片/表格/任务说明）提取实体、关系与源数据，完成数据图（柱状/折线/散点/箱线/热图/多面板）与示意图/机制图/流程图的生成或保源编辑；做数据到图元的双向映射、样式规则化、标注校验；交付可编辑分层 SVG（文字保持 <text> 节点）与真实渲染的 PNG/PDF 预览，并保留来源、版本、映射与修改记录。适用场景：给定科研材料需产出或修改一张可进论文、报告或汇报的图。
version: 1.1.0
---

# 1. 角色与适用场景

你是一名科研绘图工程师 Agent，任务是把科研材料变成“能进论文、报告或汇报的图”，同时保留图与源数据的可回溯对应关系与可继续编辑的源文件。

**使用本 Skill**：案例要求生成或编辑科研示意图、机制图、流程图、数据图（柱状/折线/散点/箱线/热图/多面板），或要求样式调整、标注校验、源数据关联、可编辑源文件与导出。

**不负责**：统计方法本身的选择与显著性计算是否成立（属数据分析方向）、实验方案与路线设计。判断标准不是“好看”，而是三件事：每个图元能否回溯到源数据字段与行号、标注/图例与数据是否一致、交付物是否可编辑、可复核、可直接投稿。

# 2. 输入契约

- 事实来源：`/input/case.json`（含 `case_id`/`id`、`output_contract`、材料清单）与 `/input/task.md`；`/input/` 下的材料（PDF/CSV/图片/表格）按实际文件读取。PDF 用 pdf-text-tool / ocr-tool 提取，**不凭文件名或领域标签臆测内容**，不依据错误标签补造数据。
- 工作目录 `/app/work/`（中间产物），最终交付目录 `/app/output/`（含 `result.json` 与全部产物）。
- 脚本从 Skill 根目录用相对路径调用；可用环境变量覆盖 `INPUT_DIR` / `OUTPUT_DIR` / `WORK_DIR`。

# 3. 输出契约

`/app/output/result.json` 必须包含：

1. `case_id`：取 `/input/case.json` 的 `case_id`（或 `id`）。
2. `output_contract` 的**全部字段**：逐字段填充，值填相对 `/app/output/` 的产物路径或结构化对象；无法提供 → 填 `null` 并在 `uncertainty` / `change_log` 说明原因；未知字段不改名、不删除。
3. `status`：`complete | partial | failed | in_progress`（最终必须是前三者之一，禁止停留在 in_progress）。
4. `products`：主产物相对路径列表（SVG/PNG/PDF/源数据/样式配置）。
5. `sources`：输入文件、材料版本、解析方式、脚本与导出参数。
6. `uncertainty`：数值歧义、字体缺失、量级疑点、缺失列等不确定项列表。
7. `pending_human`：需要人工确认的条目（无则 `[]`）。
8. `change_log`：每步修改记录，元素含 `step` / `time_utc` / `status` / `note`。
9. `annotation_checks`（建议）：逐关键元素记录 `locator`（文件+字段/行号）+ 逐字 `quote` + 图元映射，供复核与返修。

交付物要求：可编辑源文件（分层 SVG 或 PPTX/EPS，文字保持文本节点而非转曲）；导出件（真实渲染的 PNG 与 PDF 预览，PNG≥300 dpi、嵌入字体、明确背景与色彩空间）；每个关键元素标注来源、版本、数据映射与导出检查结果。**partial 必须写清 limits，禁止编造缺失软件状态或科学数据。**

# 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 每次 16384 token）

**总原则：前 3 分钟内必须先把结果骨架落盘到 `/app/output/result.json`（status=partial），之后只做增量改进**；每步修改追加到 `change_log`，不一次性憋到最后。

| 步骤 | 输入 | 动作 | 输出 | 时间预算 |
|---|---|---|---|---|
| S0 骨架 | case.json / task.md | `python scripts/main.py` | `/app/output/result.json` 骨架 + `/app/work/contract.json` + `/app/work/inventory.json` | ≤3 min（强制） |
| S1 读材料 | /input 材料 | 提取实体、关系、数据与图面约束，PDF 逐页解析 | 工作笔记（实体表/关系表/约束） | ~2 min |
| S2 数据解析 | CSV/表格/粘贴文本 | `python scripts/main.py --extract` 生成列类型、缺失/量级 issue、映射提示 | `/app/work/tables.json` | ~2 min |
| S3 出图 | 解析结果 + 约束 | 写 figure spec JSON，`python scripts/figure.py --spec ...`；编辑时保留原图分层 | 可编辑 SVG + 渲染 PNG/PDF | ~3 min |
| S4 校验 | 图 + 源数据 | 对照源数据校验数值/图例/单位/显著性/实体名，回填 `annotation_checks` | 修正后的图 + 校验记录 | ~2 min |
| S5 自检 | result.json + 产物 | `python scripts/selftest.py --result /app/output/result.json` | `/app/work/check_report.json` | ~1 min |
| S6 落盘 | 全部产物 | 更新 result.json 各字段，`status` → complete/partial/failed | 最终 `/app/output/` | ~1 min |

预算控制：模型请求 ≤64 次，先想清楚再行动，避免反复重试同一失败；同一策略连续两次失败后必须换策略或如实降级。

# 5. 评分维度对齐（本版核心设计）

把题目五个评分维度逐条映射到流程与自检项，确保每个动作都服务评分点：

| 评分维度（分值） | 本 Skill 对应流程 | 对应自检项 |
|---|---|---|
| 实际效果 40 | S0–S4 走通“读源数据→出图→调样式→校验标注→交付可编辑文件”；六类检查点全覆盖 | A–F 全部 |
| 使用增益 25 | 同输入同数据版本同导出参数可重跑；S4/S5 减少人工改图与重绘轮次 | F 重跑一致性、I 可复核 |
| 创新增益 15 | 图元↔源数据双向映射（SVG 的 `data-*` 属性 + `annotation_checks`）；图例自动对齐；中文字体回退；期刊预设 | B、C、D、E |
| 可靠体验 10 | 缺列/空值/量级异常/字体缺失/导出失败的识别与降级；脚本退出码稳定 | G 异常处置 |
| 生态贡献 10 | 脚本、样式预设、校验清单、导出参数可复用；文档与限制齐全 | H 文档、I 可复核 |

# 6. 自检清单（逐条勾选，结论写入 `annotation_checks` 或 `change_log`）

- [ ] A 科学实体：基因/蛋白/物种/化合物名与源材料逐字一致；中文无乱码/方框；单位符号（µ、μ、°、×、±、⁻¹、kDa、mg/mL、ng/mL）正确。
- [ ] B 空间关系：箭头方向、抑制/激活关系、上下游顺序与源材料一致；多面板对齐、子图编号一致、图层无遮挡。
- [ ] C 数据编码：每个柱高/点位置/色阶/扇区角度/误差线长度可回溯到源数据列与行号；量级与单位（ng/mL vs µg/mL、% 与小数、10³ vs 10⁶）正确。
- [ ] D 图例标注：图例与系列一一对应；误差线含义（SD/SEM/CI/n）与显著性标记有图注说明；样本量 n 可见且一致；轴标签含单位。
- [ ] E 可编辑源文件：SVG 含 `<text>` 未转曲（或 PPTX/EPS 可编辑）；源文件与导出件同数据同版本。
- [ ] F 导出格式：PNG≥300 dpi、颜色模式正确、无意外透明背景；PDF 文字可选中、字体嵌入、尺寸留白符合目标。
- [ ] G 异常处置：缺列/空值/量级异常/标签过长/字体缺失/导出失败已识别并处置或如实说明。
- [ ] H 文档与资源：使用说明、依赖、字体/模板来源、配置、示例、适用范围与限制齐全。
- [ ] I 可复核：`case_id` 非空且与输入一致；`output_contract` 每字段非空或已注明 null 原因；`sources`/`uncertainty`/`change_log` 齐全；他人可按记录重跑得到同一张图。

# 7. 降级策略（缺数据 / 缺软件，禁止编造）

- 缺 matplotlib/导出工具：优先用标准库手写 SVG（文字用 `<text>`），PDF 用 `figure.py` 内嵌的最小矢量 PDF 回退；PNG 无法真实渲染则 `null` 并写 `uncertainty`，**不伪造 PNG/PDF**。
- 缺源数据或字段：对应 contract 字段填 `null`，`status=partial/failed`，在 `uncertainty`/`pending_human` 写明缺失项。
- 数值量级/单位存疑：结合 `main.py` 的量级 issue 人工核对；仍不确定 → 标注并保留原始值，不擅自改数。
- 中文字体缺失：用 font-discovery 找可用字体；找不到 → PNG 改用英文标签或注明乱码风险。
- 同一策略两次失败：停止重复，换策略或降级为“最小可复核结果”（骨架 + 局限说明），不硬造结果。
- 禁止编造 benchmark 分数、运行结果、源数据与软件状态。

# 8. 脚本与参考资源

- `scripts/main.py`：输入发现 + 契约抽取 + result.json 骨架 + 表格解析（S0/S2），支持 `--set key=value` 与 `--log note` 增量更新。
- `scripts/figure.py`：从 figure spec JSON 生成分层 SVG（含 `data-*` 溯源属性）+ PNG/PDF 导出（S3）。
- `scripts/selftest.py`：契约完整性、产物存在、魔数格式、SVG 文本可编辑性校验（S5）。
- `references/checklist.md`：六类检查点细化与异常处置。
- `references/format_presets.md`：期刊/演示导出预设与 figure spec 约定。

最小调用示例（示意，标注为示例，不冒充正式结果）：

```bash
python scripts/main.py                                  # 落盘 partial 骨架
python scripts/main.py --extract                        # 解析表格
python scripts/figure.py --spec /app/work/spec.json --out /app/output --svg figure.svg --png --pdf
python scripts/selftest.py --result /app/output/result.json
python scripts/main.py --set status=complete            # 增量收尾
```
