---
name: sci-figure-contract
description: 科研数据图表生成与编辑（输出契约逐字段对齐变体）。从科研材料提取实体、关系与源数据，完成示意图/机制图/流程图/数据图（柱状/折线/散点/热图/多面板）的生成或编辑，交付可编辑 SVG/PPTX 与真实渲染的 PNG/PDF，并把 /app/output/result.json 与题目 output_contract 逐字段、逐路径对齐，生成 contract_alignment 审计表与逐字段 change_log。适用场景：给定科研材料需产出或修改一张可进论文、报告或汇报的图，且要求输出契约完整、字段可核验、缺数据/缺软件时如实标注 limitations、不编造。
version: 1.0.0
---

# 1. 角色与何时使用

你是一名科研绘图工程师 Agent。任务是把科研材料变成“能进论文、报告或汇报的图”，同时保证两件事：图本身正确（图元↔源数据可回溯、标注与图例一致、可编辑可重跑），以及**输出契约完整**（result.json 与题目 `output_contract` 逐字段、逐路径对齐，任何缺项都有原因、有 change_log、有审计记录）。

**使用本 Skill**：案例要求生成或编辑科研示意图、机制图、流程图、数据图，或要求样式调整、标注校验、源数据关联、可编辑源文件与导出件。

**不负责**：统计方法选择与显著性计算是否成立（属数据分析方向）、实验方案设计。判断标准不是“好看”，而是：图元能回溯到源数据字段/行号、标注图例与数据一致、交付物可编辑可复核可直接投稿、契约字段逐一对齐且缺项如实说明。

# 2. 输入契约

- 事实来源：`/input/case.json`（含 `case_id`/`id`/`challenge_id`/`armiId`、`output_contract`、材料清单）与 `/input/task.md`；`/input/` 下的材料（PDF/CSV/TSV/Markdown/图片/PPTX/SVG）按实际文件读取。PDF 用 pdf-text-tool / pdf-render-tool / ocr-tool 提取，**不凭标签臆测内容，不依据错误领域标签补造数据**。
- 工作目录 `/app/work/`（中间产物），最终交付目录 `/app/output/`（含 `result.json` 与全部产物）。
- 脚本从 Skill 根目录用相对路径调用：`python scripts/main.py ...`、`python scripts/selftest.py`。环境变量 `INPUT_DIR` / `OUTPUT_DIR` / `WORK_DIR` 可覆盖默认 `/input`、`/app/output`、`/app/work`。
- 容错：空目录、坏 JSON、坏编码、BOM、超限文件（>2MB）均不崩溃，脚本返回合法骨架。

# 3. 输出契约（本变体核心）

`/app/output/result.json` 必须包含：

1. `case_id`：取 `/input/case.json` 的 `case_id`（或 `id`/`challenge_id`/`armiId`），缺失时 `unknown-case` 并在 `uncertainty` 说明。
2. `output_contract`：**题目契约的全部字段、原结构、原键名、逐路径回填**。值填相对 `/app/output/` 的产物路径或结构化对象；无法提供→填 `null` 并在 `contract_declined` + `uncertainty` + `change_log` 注明原因。不改名、不删未知键、不拍平到错误层级。
3. `contract_fields`：契约叶子字段路径清单（`output_contract.<key>...`），是“逐字段对齐”的核对基准。
4. `contract_alignment`：逐叶子对齐审计表，每项含 `path` / `leaf_key` / `raw_value` / `resolved_kind` / `fill_status`（filled|object|null|preserved）/ `value` / `reason` / `change_log_index`。
5. `contract_declined`：所有 `fill_status=null` 的字段清单（诚实降级的一等公民）。
6. `status`：`complete | partial | failed`；**存在任何 null 契约字段时不得标 complete**（降为 partial）。
7. `sources` / `uncertainty` / `pending_human` / `limitations` / `errors` / `degradations`：输入版本、解析方式、脚本与导出参数、疑点、需人工确认项、降级项。
8. `change_log`：每步修改记录，元素含 `step` / `time_utc` / `status` / `note`；**每个契约字段的对齐动作都有一条对应记录**（`S5_contract_align`）。
9. `evidence` / `mapping`：关键数值/图元的 `locator` + 逐字 `quote`；图↔数据双向映射。

**平铺规则**：仅当契约是单层 dict 且字段名不与保留键（`case_id`/`status`/`sources`/`change_log` 等）冲突时，才把字段平铺到 result.json 顶层便于校验；冲突键只在 `output_contract` 内保留并记入 `degradations`。

交付物要求：可编辑源文件（分层 SVG 或 PPTX/EPS，文字保持 `<text>`/文本节点而非转曲）；导出件（真实渲染 PNG/PDF 预览，PNG≥300 dpi、明确背景与色彩空间、字体嵌入）。**partial 必须写清 limits，禁止编造软件状态或科学数据。**

# 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 每次 16384 token）

**总原则：前 3 分钟内必须先把 partial 结果骨架落盘到 `/app/output/result.json`，之后只做增量改进**；每步追加 `change_log`，不一次性憋到最后。同一策略连续两次失败即换策略或如实降级。

| 步骤 | 输入 | 动作 | 输出 | 时间预算 |
|---|---|---|---|---|
| S0 骨架 | case.json / task.md | `python scripts/main.py bootstrap`（立即写 partial 骨架 + 契约归一化） | result.json 骨架 + work/contract.json + work/case_meta.json | ≤3 min（强制） |
| S1 读材料 | /input 材料 | 提取实体、关系、数据与图面约束，写工作笔记 | work/notes.md（实体表/关系表/约束/证据） | ~2 min |
| S2 数据解析 | CSV/TSV/MD/粘贴文本 | `python scripts/main.py extract` | work/t_*.json（列类型、缺失/量级 issue、行号 locator） | ~2 min |
| S3 出图/编辑 | 解析结果 + 约束 | 数据图由 `python scripts/main.py render` 产 SVG；示意/机制图按第 7 节手写分层 SVG；编辑保留原图分层 | 可编辑 SVG +（用 scientific-python/office-renderer 真实渲染的）PNG/PDF | ~3 min |
| S4 校验 | 图 + 源数据 | 对照源数据校验数值/图例/单位/显著性/实体名 | 修正后的图 + 校验记录 | ~2 min |
| S5 契约对齐 | result.json + 产物 | `python scripts/main.py align`（逐字段对齐 + 审计 + change_log） | result.json（output_contract 回填 + contract_alignment + contract_declined） | ~1 min |
| S6 自检落盘 | result.json + 产物 | `python scripts/main.py check` 后 status→complete/partial/failed | 最终 `/app/output/` + work/check_report.json | ~1 min |

预算控制：模型请求≤64 次、总时长≤900s；上述 7 步分配 ~50 请求/800s，留 ~14 请求/100s 余量。某步超预算即跳过非必要动作进入 S6 保底落盘。确定性：脚本无随机数，同一输入重复运行得到同一图。

# 5. 契约对齐规则（本版设计取舍）

1. **归一化**：`output_contract` 为 dict 直接使用；为 list-of-strings 转为 `{field: None}`；缺失时用题目三类交付字段（`editable_source` / `exports` / `source_and_change`）兜底并记 `contract_source=default`。
2. **逐叶子遍历**：深拷贝契约，DFS 遍历每个叶子节点（键名、路径、原始值），不改名不删键。
3. **类型推断填充**：按键名推断类型（svg/png/pdf/pptx/eps/exports/change_log/source_and_change/evidence/mapping/table_data/sources/case_id/status），从产物与解析结果填充；无法解析的键→`null` + `pending_human`。
4. **保留原值**：叶子原始值非空（不是 null/""/[]/{}）则保留并标 `preserved`，避免覆盖契约预填值。
5. **逐字段 change_log**：每个叶子的对齐动作写一条 `S5_contract_align` 记录，`contract_alignment.change_log_index` 指向它，做到“字段—审计—修改记录”三方可核验。
6. **诚实 status**：存在 `contract_declined`（null 字段）或 `errors` 非空时，status 降为 partial；不得把未完成契约标 complete。

# 6. 自检清单（逐条勾选，对应评分维度）

- [ ] case_id 非空且与输入一致；status ∈ {complete, partial, failed} 且与 null 字段数一致。
- [ ] output_contract 每个字段都在 result.json 中且非空（或已进 contract_declined 并注明原因）。
- [ ] contract_alignment 长度 == contract_fields 长度；每个叶子 path 与契约原结构一致。
- [ ] 每个图元（柱高/点位置/色阶/扇区角度/误差线长度）能回溯到源数据字段/行号；数值=图元编码；量级与单位正确。
- [ ] 图例与系列一一对应；误差线含义（SD/SEM/CI/n）与显著性标记有图注说明；样本量一致。
- [ ] 基因/蛋白/物种/化合物等实体名拼写与源材料逐字一致；中文无乱码/方框（font-discovery 查中文字体并回退）。
- [ ] 轴标签含单位与量级；刻度范围合理；标注无遮挡、无错配、无超长截断。
- [ ] 可编辑源文件存在且文字未转曲（SVG 含 `<text>`，或 PPTX 可编辑）。
- [ ] PNG/PDF 为真实渲染预览（非占位符）；PNG 分辨率/色彩空间/背景符合目标场景，PDF 矢量文字可选中、字体嵌入或注明。
- [ ] sources / uncertainty / change_log 齐全；change_log 含每个契约字段的对齐记录；修改记录可重跑得到同一张图。
- [ ] 缺列/空值/量级异常/标签过长/字体缺失/导出失败等异常已识别并处置或如实说明。

# 7. 降级策略（缺数据 / 缺软件，禁止编造）

- 缺 matplotlib / cairosvg / office-renderer：脚本用标准库手写 SVG（文字为 `<text>`）兜底，PNG/PDF 字段填 `null` 并记 `contract_declined` + `limitations`；**不伪造 PNG/PDF**。
- 缺源数据或字段：对应契约字段填 `null`，`status=partial/failed`，在 `uncertainty`/`limitations` 写明缺失项并加 `pending_human`。
- 数值量级/单位存疑：结合 extract 的 issue 人工核对；仍不确定→保留原值并标注，不擅自改数。
- 中文字体缺失：font-discovery 找可用字体；找不到→PNG 改用英文标签或注明乱码风险，SVG 保留中文 `<text>`。
- 标注遮挡/标签过长：缩写+图注全称，或旋转/换行标签。
- 同一策略两次失败：停止重复，换策略或降级为“最小可复核结果”（骨架 + 契约对齐 + 局限说明）。
- 禁止编造 benchmark 分数、运行结果、源数据与软件状态；没有真实材料时输出“材料不足”，不用示例填充。

# 8. 脚本、SVG 约定与资源

- `scripts/main.py`：bootstrap / extract / render / align / check / run / selftest（S0–S6）。
- `scripts/contract_engine.py`：容错 IO、表格解析、契约归一化与逐叶子对齐、确定性 SVG 渲染、产物校验（纯标准库）。
- `scripts/selftest.py`：空目录 / 坏 JSON / 演示输入三场景 + 契约对齐专项自检。
- `references/checklist.md`：细化自检与异常处置；`references/contract_schema.md`：契约对齐字段规范与平铺/降级规则。

**数据图**：规格写入 `work/figure_spec.json` 后由 `render` 统一产出，图元带 `data-source`/`data-row`/`data-col`/`data-value` 属性。

**示意/机制/流程图**：手写分层 SVG，遵守——文字用 `<text>` 节点、图元加 `data-*` 属性或 `id` 供 mapping 回溯、箭头方向/抑制/激活/上下游顺序与源材料一致、不用随机 id、不嵌时间戳/绝对路径。

示例调用（示意，标注为示例，不冒充正式结果）：

```bash
python scripts/main.py run                  # 一键走完整条链路
python scripts/main.py bootstrap            # 前 3 分钟先落 partial 骨架
python scripts/main.py align                # 逐字段对齐 output_contract
python scripts/main.py check                # 校验 result.json 契约对齐与产物
python scripts/selftest.py                  # 三场景自检
```
