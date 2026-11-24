---
name: apparatus-route-figure
description: >-
  从装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分中抽取"实体—连接—步骤—流向"结构化中间表示（IR），
  经确定性布局与自检生成可编辑、分层、可溯源（source_ref）的 SVG 装置图/机制图/流程图/技术路线图，并诚实导出 PNG/PDF 与
  /app/output/result.json。鲁棒性变体：空输入、坏 JSON、缺参数、超大文件、缺字体/渲染工具均优雅降级不崩溃。
version: 1.0.0
---

# apparatus-route-figure：实验装置与技术路线科研绘图 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 先构建"实体—连接—步骤—流向"结构化中间表示（IR），再布图、生成可编辑 SVG、自检与导出，避免只输出文字或单张截图、凭空补齐管路、箭头反向、图例与图形不一致等问题。判断做对看三件事：**图上每个实体能否追溯到输入材料、连接与先后顺序是否成立、交付物能否被打开、修改并复跑出同一张图**。

- **适用**：输入含装置清单、装配说明、方法段落、工艺/流程描述、研究内容与阶段划分，需产出装置图、机制图、流程图或技术路线图。
- **不适用**：数据分析与数值拟合；判断实验方案本身是否科学合理；论文排版与期刊投稿格式适配。
- **本版侧重（变体 #鲁棒性）**：空输入、坏 JSON、缺参数、超大文件、缺字体/渲染工具都要优雅降级不崩溃；在保持六类要素完整性的前提下做防御式输入处理与确定性复跑。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（案例说明、输入约束、`output_contract`）、`/input/task.md`（题面）；材料与图件按 `case.json` 指引读取。
- PDF 用 `pdf-text-tool` / `pdf-render-tool` / `ocr-tool` 解析实际文件，**不依据标签假设内容**；图表数据以实际源数据为准。
- 工作目录 `/app/work`；最终产物写 `/app/output/`（含 `result.json`）。
- 工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。
- 脚本在 Skill 根目录 `scripts/`；先 `cd /skills/weekly-skill`，用相对路径调用，例如 `python scripts/main.py run`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 JSON），包含：

- `case_id`：取自 `/input/case.json`。
- `output_contract`：题面声明的字段**原样回填全部键**（缺值给 `null`/`[]`，不改名、不删未知键）；未声明时用默认六类字段。
- 默认六类要素（同时镜像到顶层，便于判分读取）：
  - `entities`：科学实体（id、label、type、source_ref、note、confidence）。
  - `relations`：空间/流向关系（from、to、kind、direction、source_ref）。
  - `data_encoding`：数据映射（数值/参数 → 颜色、线宽、符号、编号）。
  - `legend_annotations`：图例与标注映射（key → 颜色/线型/符号及文字）。
  - `editable_sources`：可编辑源文件清单（path、format、layers、bytes、notes）。
  - `exports`：导出件清单（path、format、status、renderer、reason）。
- 通用补充字段：`status`（`in_progress|complete|partial|insufficient_evidence|input_error|failed`）、`sources`（`locator`+逐字 `quote`）、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`checks`、`artifacts`、`runtime`。
- 关键约定：可编辑源文件优先交付**分层 SVG**，并复制到 `/app/output/figure.svg`；SVG 中每个实体/关系/图例/标注带 `source_ref` 数据属性；SVG 末尾以 `<script id="ir-json">` 内嵌 IR 支持复现；PNG/PDF 渲染失败必须写 `skipped`，禁止伪造。

## 4. 执行流程（总预算 900 秒、64 次模型请求、每响应含推理 ≤16384 tokens）

**硬性要求**：前 3 分钟（约 180 秒）内必须先落盘 `result.json` 骨架（`status=in_progress`，含 `case_id`、契约字段、首条 `change_log`），之后只做增量改进；任何时刻均可交付诚实的部分结果。每步给"输入→输出与时间预算"：

1. **读输入（≤90s）**：读 `/input/case.json` + `task.md` → 确定案例目标、图种（kind）、材料路径、`output_contract` 字段；`python scripts/main.py init --case /input/case.json --out /app/output/result.json` 立即落盘骨架。
2. **提取 IR（≤150s）**：从材料抽取 entities/relations/steps/stages，逐条附 `source_ref`（`locator`+逐字 `quote`）→ 写 `/app/work/ir.json`（字段见 `references/ir_schema.md`）。
3. **布图生成 SVG（≤180s）**：`python scripts/main.py build --ir /app/work/ir.json --out /app/work/figure.svg`；坐标缺省由脚本确定性分层/网格布局并在 meta 标注 `auto-layout`，随后模型微调坐标再重跑。
4. **渲染预览（≤120s）**：`python scripts/main.py run` 会调用可用渲染工具导出 PNG/PDF；导出失败写入 `degradations/limitations`，不编造。
5. **自检（≤120s）**：`python scripts/main.py validate --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/work/checks.json`；critical 项不过则修订 IR/SVG（最多 2 轮）。
6. **收尾（≤120s）**：`python scripts/main.py run` 合并六类字段、sources/uncertainty/checks，`status` 按证据诚实定为 `complete|partial|insufficient_evidence|input_error|failed`，写 artifact 清单与结束时间。

剩余时间用于增量改进（修正连接方向、补充图例、重渲染、复核 source_ref），每次改动后更新 `change_log`。

## 5. 自检清单（逐条对应评分维度）

- [ ] 实际效果·完成度：交付可编辑分层 SVG + 真实渲染预览，不是纯文字或单张截图。
- [ ] 实际效果·正确性：部件名称/数量/接口与材料一致；连接方向正确、箭头无反；数值标注与来源一致。
- [ ] 实际效果·完整性：六类要素齐全；`output_contract` 全部字段原样回填。
- [ ] 实际效果·可核验：SVG 含 `source_ref`；result.json 含 `sources`（`locator`+`quote`）与 `checks`，可独立复跑。
- [ ] 使用增益：IR→布图→自检流水线可复用，返工与人工修改量下降。
- [ ] 创新增益：沉淀 IR 中间表示、确定性布局、图例/编号规则（见 `references/ir_schema.md`）。
- [ ] 可靠体验：脚本仅标准库、相对路径、异常有降级；重复运行结果一致。
- [ ] 生态贡献：说明、输入约定、图元规范、限制与失败清单齐全。

完整勾选清单与失败样例见 `references/checklist.md`；运行 `python scripts/selftest.py` 验证五个健壮性场景。

## 6. 降级策略（缺数据/缺软件时必须如实标注 limitations，禁止编造）

| 场景 | 处置 |
|---|---|
| 空输入目录 / 材料缺失 | `status=insufficient_evidence`，缺口写入 `pending_human`，退出码 0，不抛异常 |
| 坏 JSON / 坏编码 | 记录到 `errors`/`degradations`，继续处理其余材料（utf-8-sig → utf-8 → gb18030 → latin-1 容错） |
| 缺参数 / 未知参数 | `--input/--work/--out` 缺省指向 `/input`、`/app/work`、`/app/output`；未知参数不中断主流程 |
| 单文件 > 2MB | 跳过并记录 `oversized(...)`，不整文件读入 |
| 渲染工具缺失 / 失败 | 只交付 SVG，`exports[].status="skipped"`，写 `limitations`，不伪造 PNG/PDF |
| 字体缺失（中文方框） | 用 `font-discovery` 查字体；缺中文字体时在 `fonts`/`limitations` 标注并降级英文标签 |
| 画布溢出 / 接口冲突 / 断头箭头 | `validate` 报 `canvas_overflow` / `relation_*_missing`；标记 `uncertainty` 与 `pending_human`，优先遵循材料原文，不改写科学内容 |
| 脚本报错 | 读 stderr 定位；同一策略失败两次即换策略，错误写入 `change_log` 与 `limitations` |

## 7. 脚本与资源（相对路径，仅标准库，确定性）

- `scripts/main.py`：入口，子命令 `run`（完整流水线）/ `init` / `build` / `validate`。
- `scripts/core_impl.py`：输入发现、IR 规范化、确定性布局、SVG 构建、校验、渲染探测、result 组装。
- `scripts/selftest.py`：空输入、坏 JSON、缺参数、超大文件、正常路径五场景自检（合成样例，非正式测试）。
- `references/ir_schema.md`：IR 字段、图元与布图规范。
- `references/checklist.md`：可勾选检查清单与常见失败样例。

最小调用示例（示意，非正式案例）：`python scripts/main.py run --input /input --work /app/work --out /app/output`。
