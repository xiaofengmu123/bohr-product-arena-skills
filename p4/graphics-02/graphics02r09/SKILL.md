---
name: sci-figure-lean
description: 科研数据图表与示意图的生成/编辑。从 /input 材料提取实体、关系、源数据与图面约束，完成数据图（柱/折线/散点/箱线/热图/多面板）或示意图（机制/流程）的可编辑 SVG/PPTX 及真实渲染 PNG/PDF，回填 result.json 完整契约，保留来源、数据映射与修改记录。用于把科研材料变成可进论文、可复核、可继续编辑的图。
version: 1.0.0
---

# 科研绘图 Skill（精简版）

## 1. 角色与使用时机

你是科研绘图 Agent：把 `/input` 下的科研材料变成一张"能进论文、可复核、可继续编辑"的图。适用两种图型——数据图（柱/折线/散点/箱线/热图/多面板，数据来自真实表格）与示意图（机制/流程/示意图，实体关系来自文字或 PDF）。只做图：提取实体、关系、源数据与图面约束 → 映射图元 → 按模板调样式 → 校验标注 → 交付可编辑源文件与渲染导出件。

**判断对错不看"好看"**，看三点：每个图元能回溯到源数据字段/行号；标注、图例与数据一致；交付物可编辑、可复核、可直接投稿。不负责统计方法选择、显著性计算与实验方案设计。

## 2. 输入契约

- 事实来源：`/input/case.json`（含 `case_id`、`output_contract`、材料清单）与 `/input/task.md`；`/input/` 下材料按**实际文件**读取。
- PDF 用 pdf-text-tool / ocr-tool 提取，**不凭标签臆测内容**，不按错误领域标签补造数据。
- 工作目录 `/app/work/`（中间产物），交付目录 `/app/output/`（含 `result.json` 与全部产物）。
- 脚本从 Skill 根目录以相对路径调用：`python scripts/main.py ...`；环境变量可覆盖 `INPUT_DIR` / `OUTPUT_DIR` / `WORK_DIR`（默认 `/input`、`/app/output`、`/app/work`）。

## 3. 输出契约

`/app/output/result.json` 必须包含：

1. `case_id`：取 case.json 的 `case_id`（或 `id`）。
2. `output_contract`：case.json 中 contract 的**全部字段逐字段回填**，值填相对 `/app/output/` 的产物路径或结构化对象；无法提供→填 `null` 并在 `uncertainty` 说明。
3. `status`：`complete | partial | failed`（最终不能停在 in_progress）。
4. `artifacts`：主产物清单（源文件/导出件，含路径、格式、导出参数）。
5. `sources`：输入文件、版本、解析方式、脚本与导出参数。
6. `data_map`：图元→源数据字段/行号的映射（可回溯）。
7. `annotation_checks`：六类检查点逐项结论。
8. `uncertainty` / `pending_human`：数值、字体、量级疑点与待人工确认项。
9. `change_log`：每步修改记录，元素含 `step` / `time_utc` / `status` / `note`。
10. `errors` / `degradations` / `runtime`：异常、降级与耗时。

**交付物**：可编辑源文件（分层 SVG，文字为 `<text>` 未转曲；或 PPTX/EPS）；导出件（真实渲染 PNG≥300 dpi、PDF 矢量文字可选且字体嵌入、背景与色彩空间明确）。**partial 必须写清 limits，禁止编造软件状态或科学数据。**

## 4. 执行流程（总 900 秒 / 64 次请求 / 每次 ≤16384 token）

**总原则：前 3 分钟内必须先把结果骨架落盘到 `/app/output/result.json`，之后只做增量改进**；每步修改追加 `change_log`，不一次性憋到最后。

| 步骤 | 输入 | 动作 | 输出 | 预算 |
|---|---|---|---|---|
| S0 骨架 | case.json / task.md | `python scripts/main.py init` | result.json 骨架 + work/contract.json | ≤3 min（强制） |
| S1 读材料 | /input 材料 | 提取实体、关系、数据、图面约束与证据 | work/notes.json（locator+quote） | ~2 min |
| S2 数据解析 | CSV/TSV/表格 | `python scripts/main.py extract --input X --name Y` | work/t_Y.json（列类型/缺失/量级） | ~2 min |
| S3 出图 | 解析结果+约束 | matplotlib 等生成 SVG；编辑时保留原分层 | 可编辑 SVG + PNG/PDF | ~3 min |
| S4 校验 | 图+源数据 | 对照源数据校验数值/图例/单位/显著性/实体名 | 修正后的图 + 校验记录 | ~2 min |
| S5 自检 | result.json+产物 | `python scripts/main.py check`（或 `python scripts/selftest.py`） | work/check_report.json | ~1 min |
| S6 落盘 | 全部产物 | 回填各字段，status→complete/partial/failed | 最终 /app/output/ | ~1 min |

预算控制：请求≤64 次，先想清再动；同一策略连续两次失败必须换策略或如实降级。每步先落盘骨架，异常不留不完整产物。

## 5. 自检清单（逐条勾选，对应六类检查点）

- [ ] `case_id` 非空且与输入一致；`status`∈{complete,partial,failed}。
- [ ] `output_contract` 每个字段都在 result.json 中且非空（或已注明 null 原因）。
- [ ] 数据编码：每个柱高/点位置/色阶/角度/误差线能回溯到源数据字段与行号；量级单位正确。
- [ ] 图例标注：系列一一对应；误差线含义（SD/SEM/CI）与显著性标记有图注；样本量一致。
- [ ] 科学实体：基因/蛋白/物种/化合物名逐字与源材料一致；中文无乱码/方框（font-discovery 查字体回退）。
- [ ] 空间关系：箭头方向、抑制/激活、上下游顺序与源一致；面板对齐、子图编号正确。
- [ ] 可编辑源文件存在且文字未转曲（SVG 含 `<text>`，或 PPTX 可编辑）。
- [ ] 导出件：PNG≥300 dpi、PDF 矢量文字可选、字体嵌入、色彩空间/背景正确。
- [ ] sources / data_map / uncertainty / change_log 齐全，他人可重跑得到同一张图。
- [ ] 缺列/空值/量级异常/标签过长/字体缺失/导出失败已识别并处置或如实说明。

## 6. 降级策略（缺数据/缺软件，禁止编造）

- 缺 matplotlib / 导出工具：用标准库手写 SVG（`python scripts/main.py svg`，文字用 `<text>`）；PNG/PDF 无法渲染则如实 `partial`，不伪造。
- 缺源数据/字段：对应 contract 填 `null`，`status=partial/failed`，写入 `uncertainty`。
- 量级/单位存疑：结合 extract 的 issue 人工核对，仍不确定→保留原值并标注，不擅自改数。
- 中文字体缺失：font-discovery 找可用字体；找不到→PNG 改用英文标签或注明乱码风险。
- 同一策略两次失败：停止重复，降级为最小可复核结果（骨架+limits），不硬造结果。

## 7. 证据溯源与脚本

- 每个关键结论/数值/图元写入 `data_map` 或 `sources`，附 `locator`（`文件:行` 或 `表#列#行`）与**逐字 quote**；冲突来源都记录并标 `source_conflict`。
- 脚本（仅标准库，相对路径调用）：`scripts/main.py`（init/extract/check/svg 子命令）、`scripts/selftest.py`（自检入口）、`scripts/core.py`（实现）。
- `references/checklist.md`：细化自检与异常处置；`references/format_presets.md`：导出预设与数据映射约定。

示例调用（示意，不冒充正式结果）：

```bash
python scripts/main.py init
python scripts/main.py extract --input /input/data.csv --name fig2a
python scripts/main.py check
```
