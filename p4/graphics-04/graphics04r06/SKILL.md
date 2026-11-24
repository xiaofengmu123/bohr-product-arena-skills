---
name: scix-figure-audit
description: 从科研材料（装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分）抽取实体、连接、步骤、材料/能量/信息流向，先落证据清单，再构建可复核的结构化中间表示 figure_model.json（科学真相）与 plan.json（确定性布局），据此生成分层可编辑 SVG 与真实渲染预览，运行自检并增量写回 result.json。核心是可追溯、可复跑、可独立核验的实验装置与技术路线绘图。
version: 1.0.0
---

# scix-figure-audit：可复核的科研装置/技术路线绘图

## 1. 角色与何时使用
本 Skill 让 Agent 先"落证据、建模型、再布图"，把科学事实与绘图决策分离：`figure_model.json` 回答"画什么、对不对"，`plan.json` 回答"画在哪、怎么连"。每一步产出结构化文件并带 `source_ref` 与逐字 `quote`，使图稿能被别人独立复核并复跑出同一张图。

- **使用时机**：输入含装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分，需产出装置图、机制图、流程图或技术路线图。
- **不适用**：数据分析与数值拟合；判断实验方案本身是否科学合理；论文排版与期刊投稿格式适配。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json`（案例说明、输入约束、`output_contract` 如声明）、`/input/task.md`（题面）。
- 科研材料与图件：按 `case.json` 指引读取；**先扫描不假设**，PDF 用 `pdf-text-tool`/`pdf-render-tool`/`ocr-tool` 解析实际文件，图表数据以实际源数据为准，不依据标签假设内容。
- 工作目录 `/app/work`；最终产物写到 `/app/output/`。
- 可用工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。
- 脚本位于本 Skill 根目录 `scripts/`；先 `cd /skills/weekly-skill`，用相对路径调用，如 `python scripts/main.py init`。

## 3. 输出契约
必须产出 `/app/output/result.json`（UTF-8 JSON），并写清：
- `case_id`：取自 `/input/case.json`（缺省 `unknown-case`）。
- 若 `case.json` 声明 `output_contract`，按其字段名**原样回填全部字段**（缺值给 `null`/`[]`，不改名、不删未知键），再补充通用字段；若未声明，采用默认六类契约字段。
- 默认契约字段（对应评分六类要素）：`entities`（科学实体 id/label/type/source_ref/note）、`relations`（from/to/kind/direction/line/source_ref）、`data_encoding`（数值/参数→尺寸、颜色、线宽、编号）、`legend_annotations`（图例与标注映射）、`editable_sources`（可编辑源文件清单 path/format/layers/notes）、`exports`（导出件清单 path/format/render_status）。
- 通用补充字段：`status`（in_progress|complete|partial|failed）、`sources`（证据定位 locator + 逐字 quote）、`uncertainty`、`pending_human`、`limitations`、`checks`、`artifacts`、`change_log`（时间戳+动作）、`runtime`。
- 关键约定：可编辑源文件优先交付**分层 SVG**；另交付真实渲染的 PNG/PDF 预览；SVG 每个实体/关系/图例/标注均带 `data-source-ref`，指回 `figure_model.json` 的 `evidence.id`，再指回材料原文。

## 4. 中间表示与可复核（本版核心设计）
与"直接让 LLM 画 SVG"不同，本 Skill 强制三段可复核中间产物，任何一段都可单独打开检查、单独重跑：

1. `inventory.json`：材料清单（实际扫描 `/input`，含 path/bytes/kind/lines；PDF 标为 binary，需工具解析）。
2. `figure_model.json`：**科学真相**。含 `entities` / `relations` / `steps` / `data_encoding` / `legend` / `evidence`；每个关键图元带 `source_ref` 指向 `evidence.id`，`evidence` 含 `locator`（如 `materials/method.md#L12-L14`）+ 逐字 `quote`。这是"对不对"的唯一权威，SVG 不承载科学判断。
3. `plan.json`：**确定性布局**。由脚本从 model 计算（`layout_engine: grid-deterministic-v1`），含 `placements`（节点位置/尺寸）、`routes`（箭头起止与虚线/回流）、`legend_entries`、`legend_box`、`canvas`、`font`。无随机数，同一 model 重复运行逐字节一致。

由此保证：SVG 图元 → `data-source-ref` → model 节点 → `evidence.id` → 材料 `locator`+`quote`，形成完整可复核链条；改科学内容只改 model，改版面只改 plan/脚本，二者互不污染。

## 5. 执行流程（总预算 900 秒、64 次模型请求）
**硬性要求**：前 3 分钟（累计 <180 秒）内必须先落盘 `result.json` 骨架（`status=in_progress`，含 `case_id`、空契约字段、首条 `change_log`），之后只做增量改进；任何时刻均可交付诚实的 partial。

| 步 | 命令/动作 | 输入 → 输出 | 时间预算 |
|---|---|---|---|
| 1 | `python scripts/main.py init` | `/input/case.json` → `result.json` 骨架 + `work/case.json` | ≤90s（累计<180s） |
| 2 | `python scripts/main.py inventory` | `/input` 目录 → `work/inventory.json` | ≤60s |
| 3 | 提取并写入 `work/figure_model.json` | 材料原文 → 实体/关系/步骤/证据（LLM 主任务，逐条附 `source_ref`+`quote`） | ≤200s |
| 4 | `python scripts/main.py model` | `figure_model.json` → 规范化 + `model_checks.json` | ≤60s |
| 5 | `python scripts/main.py plan` | model → `work/plan.json`（确定性布局） | ≤60s |
| 6 | `python scripts/main.py render` | plan → `/app/output/figure.svg`（分层可编辑） | ≤60s |
| 7 | 渲染预览 | 用 `browser-automation`/`office-renderer`/`pdf-render-tool` 把 SVG 导出 PNG/PDF | ≤120s |
| 8 | `python scripts/main.py check` | model+plan+SVG → `work/checks.json`，按结果修订（最多 2 轮） | ≤120s |
| 9 | `python scripts/main.py finalize --status complete|partial` | 合并全部产物 → `result.json` 定稿 | ≤100s |

第 3 步是唯一需要 LLM 做科学判断的步骤；第 4–9 步全部由脚本确定执行。剩余时间用于增量改进（补 `source_ref`、修连接方向、补图例、重渲染）。

## 6. 自检清单（逐条可勾选，对应评分维度）
- [ ] 已产出 `/app/output/result.json`，含 `case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`change_log` 与六类契约字段。
- [ ] 已产出可编辑分层 SVG（`layer-relations`/`layer-entities`/`layer-steps`/`layer-annotations`/`layer-legend`）。
- [ ] 已产出真实渲染 PNG/PDF 预览；缺渲染工具时在 `limitations` 说明，不假装成功。
- [ ] 每个 entity/relation/step/图例均含 `source_ref`，`evidence` 含 `locator` + 逐字 `quote`。
- [ ] 所有 relation 的 `from`/`to` 指向存在的实体或步骤，无断头箭头。
- [ ] 箭头方向与材料流向一致，回流用虚线，无反方向。
- [ ] 图例颜色/线型与关系一致，无"图例与图形不一致"。
- [ ] 数值标注与来源一致，无凭空补齐的部件、管路或数值。
- [ ] 步骤编号与正文顺序逐条对照（`order` 唯一且递增）。
- [ ] 所有节点在画布内，无溢出（`check` 会报 overflow）。
- [ ] 已运行 `python scripts/selftest.py`，critical 全部通过；未通过项已写入 `uncertainty`/`limitations`。

## 7. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
- 材料缺失/无法解析：`status=partial` 或 `failed`，在 `limitations`/`uncertainty` 标注"无法判断"，不补造部件、数值或来源。
- 坏 JSON/坏编码：`load_case` 降级为 `unknown-case` + `_read_error`，继续处理其余材料，不崩溃。
- 渲染工具缺失：只交付 SVG，`exports.render_status="skipped"`，`limitations` 说明无 PNG/PDF。
- 字体缺失（中文方框）：用 `font-discovery` 查字体；缺中文字体时在 `font` 字段标注并降级英文标签，不假装嵌入成功。
- 脚本报错：读 stderr 定位；同一策略失败两次即换策略，错误写入 `change_log` 与 `limitations`。
- 坐标/接口/来源冲突：两个来源不一致时都记录并标 `source_conflict`；优先遵循材料原文，不改写科学内容。

## 8. 脚本与资源（相对路径，仅标准库）
- `scripts/main.py`：CLI 入口，子命令 `init/inventory/template/model/plan/render/check/finalize/run`。
- `scripts/core_impl.py`：管线实现（确定性布局、SVG 生成、自检、result 管理）。
- `scripts/selftest.py`：提交前自检，`cd /skills/weekly-skill && python scripts/selftest.py`。
- `references/ir_schema.md`：`figure_model.json` / `plan.json` 字段与图元规范。
- `references/checklist.md`：可勾选清单与常见失败样例。
