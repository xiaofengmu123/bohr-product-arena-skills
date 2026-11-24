---
name: scix-figure-contract-skill
description: 从科研材料（装置清单、装配说明、方法段落、工艺/流程描述、研究内容与阶段划分）抽取实体、连接、步骤与流向，构建带逐字证据（locator+quote）的结构化中间表示（IR），按 case.json 的 output_contract 字段逐一对齐生成 result.json（含 case_id、status、sources、uncertainty、pending_human、change_log），据此布图并输出分层可编辑 SVG 与真实渲染 PNG/PDF，自检后交付可复核、可复跑的结果。适用于实验装置与技术路线科研绘图任务。
version: 2.0.0
---

# scix-figure-contract-skill：契约对齐 + 证据溯源的科研绘图

## 1. 角色与何时使用

本 Skill 让 Agent 先构建“实体—连接—步骤—流向”结构化中间表示（IR），且**每个关键图元都带 locator + 逐字 quote**，再据此布图、生成可编辑 SVG、渲染导出与自检，最后把结果逐字段写进 `result.json`。核心不是“画得像”，而是“每个元素可追溯、输出契约逐字段对齐、他人可复跑出同一张图”。

- **使用时机**：输入含装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分，需产出装置图、机制图、流程图或技术路线图。
- **不适用**：数据分析与数值拟合；判断实验方案是否科学合理；论文排版与期刊投稿格式适配。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（案例说明、输入约束、`output_contract` 如声明）、`/input/task.md`（题面）。
- 科研材料与图件：按 `case.json` 指引读取实际文件；PDF 用平台 `pdf-text-tool` / `pdf-render-tool` / `ocr-tool` 解析实际文件，**不依据标签假设内容**；图表数据以实际源数据为准。
- 工作目录 `/app/work`；最终产物写到 `/app/output/`。
- 可用工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。
- 脚本位于本 Skill 根目录 `scripts/`。先 `cd /skills/weekly-skill`，再用相对路径调用，例如 `python scripts/main.py init ...`。

## 3. 输出契约（本版核心：字段逐一对齐）

必须产出 `/app/output/result.json`（UTF-8 JSON），并满足：

- `case_id`：取自 `/input/case.json`。
- **契约字段**：若 `case.json` 声明了 `output_contract`，按其字段名**原样输出全部字段**（缺值给 `[]`/`null`，不改名、不删未知键）；未声明则用默认六类字段：`entities`、`relations`、`data_encoding`、`legend_annotations`、`editable_sources`、`exports`。
- **通用补充字段**（本版强化）：`status`（in_progress|complete|partial|failed）、`title`、`figure_kind`、`sources`（证据定位：每个源含 locator + 逐字 quote）、`uncertainty`、`pending_human`、`limitations`、`checks`、`artifacts`、`change_log`（时间戳+动作，每次改动追加）、`runtime`。
- 关键约定：可编辑源文件优先交付**分层 SVG**；另交付真实渲染 PNG/PDF 预览；SVG 中每个实体/关系/图例/标注均带 `data-source-ref`，`<metadata>` 内含 locator→quote 证据映射；`result.json` 的 `sources` 是去重后的证据表。

用 `scripts/main.py init` 先落盘骨架（自动按 output_contract 预置全部字段名），此后 `merge`/`finalize` 只做增量更新。

## 4. 执行流程（总预算 900 秒、64 次模型请求、每次含思考 16384 token）

**硬性要求**：前 3 分钟（约 180 秒）内必须先落盘 `result.json` 骨架（status=in_progress，含 case_id、全部契约字段空值、首条 change_log），之后只做增量改进；任何时刻均可交付诚实部分结果。

1. **读输入（≤90s）**：读 `/input/case.json` + `task.md` → 确定案例目标、图种（kind）、材料路径、output_contract 字段。输出：contract 字段名列表、材料路径清单。
2. **提取 IR（≤150s）**：从材料抽取 entities/relations/steps/legend/data_encoding，逐条附 `source_ref`（文件+页码/段落）与**逐字 quote** → 写 `/app/work/ir.json`（字段见 `references/ir_schema.md`）。
3. **落盘骨架（≤60s，累计须 <180s）**：`python scripts/main.py init --case-id <id> --title "<标题>" --out /app/output` → 生成骨架（此命令自动读取 /input/case.json 的 output_contract）。
4. **布图生成 SVG（≤180s）**：`python scripts/main.py build --ir /app/work/ir.json --svg /app/work/figure.svg`；坐标缺失时脚本按网格确定性布局并在 meta 标 `auto_layout`，之后微调坐标重跑（同一 IR 重复运行结果一致）。
5. **渲染预览（≤120s）**：用 `browser-automation` / `office-renderer` / `pdf-render-tool` 把 SVG 导出 PNG/PDF；导出失败写入 limitations，不编造。
6. **自检（≤120s）**：`python scripts/main.py validate --ir /app/work/ir.json --svg /app/work/figure.svg --checks /app/work/checks.json`，按结果修订 IR/SVG（最多 2 轮）。
7. **收尾（≤120s）**：复制产物到 `/app/output/`，`python scripts/main.py merge --partial /app/work/partial.json`，`python scripts/main.py finalize --status complete|partial` 写状态、结束时间与 artifact 清单。

剩余时间用于增量改进（修正连接方向、补图例、重渲染、复核 quote），每次改动后 `change_log` 自动追加。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] **完成度**：交付可编辑分层 SVG + 真实渲染 PNG/PDF，不是纯文字或单张截图。
- [ ] **正确性**：部件名称/数量/接口与材料一致；连接方向正确；箭头无反；数值标注与来源一致。
- [ ] **完整性**：六类要素 entities / relations / data_encoding / legend_annotations / editable_sources / exports 齐全。
- [ ] **契约对齐**：`result.json` 的字段名与 output_contract 完全一致（运行 `python scripts/selftest.py` 核验 contract_align）。
- [ ] **可核验**：每个关键图元带 source_ref 与逐字 quote；result.json 含 sources/checks/change_log，可独立复跑。
- [ ] **使用增益**：IR→布图→自检流水线可复用，返工与人工修改量下降。
- [ ] **创新增益**：IR 中间表示 + locator/quote 证据链 + 确定性布局规则已沉淀（见 `references/ir_schema.md`）。
- [ ] **可靠体验**：脚本仅标准库、相对路径、异常降级；重复运行结果一致。
- [ ] **生态贡献**：说明、输入约定、图元规范、限制与失败清单齐全。

## 6. 降级策略（缺数据/缺软件必须如实标注 limitations，禁止编造）

- 材料缺失或无法解析：在 `limitations` 与 `uncertainty` 标注“无法判断”，不补造部件、数值或来源。
- 渲染工具缺失：只交付 SVG，`exports.render_status="skipped"`，limitations 说明无 PNG/PDF。
- 字体缺失（中文方框）：用 `font-discovery` 查字体；缺中文字体时在 `fonts` 标注并降级英文标签，不假装嵌入成功。
- 脚本报错：读 stderr 定位；同一策略失败两次即换策略，错误写入 change_log 与 limitations。
- 坐标/接口冲突：标 `uncertainty` 并附两侧 quote，优先遵循原始材料原文，不改写科学内容。

## 7. 脚本与资源（相对路径，标准库）

- `scripts/core_impl.py`：契约发现、result.json 管理（init/merge/finalize + change_log）、IR→分层 SVG、SVG/IR 自检、渲染状态记录。
- `scripts/main.py`：命令行入口，子命令 `init|build|validate|merge|finalize|run`。
- `scripts/selftest.py`：包完整性自检（frontmatter、契约对齐、空输入降级、SVG 可解析、确定性）。
- `references/ir_schema.md`：IR 字段、图元、布局与证据溯源规范。
- `references/checklist.md`：可勾选检查清单与常见失败样例。

最小调用示例（示意输出，须标明为示例）：`python scripts/main.py run --case /input/case.json --out /app/output --work /app/work`，空材料时返回 `status=partial` 且 limitations 说明缺口，不生成示例答案。
