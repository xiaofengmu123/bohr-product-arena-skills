---
name: figure-ir-skill
description: 从科研材料（装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分）抽取"实体—连接—步骤—流向"结构化中间表示（IR），据此生成可编辑、分层、可溯源的 SVG 图稿（装置图/机制图/流程图/技术路线图），执行自检并写出可复核的 /app/output/result.json。适用于需要"画得对、可编辑、可复跑"的实验装置与技术路线绘图任务。
version: 1.0.0
---

# figure-ir-skill：实验装置与技术路线科研绘图

## 1. 角色与何时使用
- **使用**：输入含装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分，需产出装置图、机制图、流程图或技术路线图。
- **不适用**：数据分析与数值拟合；判断实验方案本身是否科学合理；期刊排版与投稿格式适配。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json`（案例说明、输入约束、`output_contract`）、`/input/task.md`（题面）。
- 科研材料与图件：按 `case.json` 指引读取；PDF 用 `pdf-text-tool` / `pdf-render-tool` / `ocr-tool` 解析**实际文件**，不依据标签假设内容。
- 工作目录 `/app/work`；最终产物写到 `/app/output/`。
- 可用工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。
- 脚本位于本 Skill 根目录 `scripts/`；先 `cd /skills/weekly-skill`，用相对路径调用（如 `python scripts/main.py init ...`）。

## 3. 输出契约
必须产出 `/app/output/result.json`（UTF-8 JSON），包含：
- `case_id`：取自 `/input/case.json`。
- 若 `case.json` 声明 `output_contract`，**原样回填其全部字段**（缺值给 `null`/`[]`，不改名、不删未知键）；未声明时采用默认六类字段。
- 默认六类契约字段：`entities`、`relations`、`data_encoding`、`legend_annotations`、`editable_sources`、`exports`。
- 通用补充字段：`status`（in_progress|complete|partial|failed）、`sources`（文件+页码/段落定位）、`uncertainty`、`pending_human`、`limitations`、`checks`、`artifacts`、`change_log`（时间戳+动作）。
- 关键约定：可编辑源文件优先交付**分层 SVG**；另交付真实渲染的 PNG/PDF 预览；SVG 中每个实体/关系/图例/标注均带 `data-source-ref` 可追溯输入材料。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）
**硬性要求：前 3 分钟（约 180 秒）内必须先落盘 `result.json` 骨架（status=in_progress，含 case_id、空契约字段、首条 change_log），之后只做增量改进；任何时刻可交付诚实的部分结果。**
1. **读输入（≤90s）**：读 `/input/case.json` + `task.md` → 确定图种 `kind`、材料路径、`output_contract` 字段。
2. **提取 IR（≤150s）**：抽取 entities/relations/steps/stages，逐条附 `source_ref` 与**逐字 quote** → 写 `/app/work/ir.json`（字段见 `references/checklist.md`）。
3. **落盘骨架（≤60s，累计<180s）**：`python scripts/main.py init --case /input/case.json --out /app/output/result.json`。
4. **布图生成 SVG（≤180s）**：`python scripts/main.py build --ir /app/work/ir.json --out /app/work/figure.svg --meta /app/work/build_meta.json`；坐标缺失时脚本给确定性网格/纵向布局并标注 auto-layout，复杂结构再微调坐标重跑。
5. **渲染预览（≤120s）**：用 `browser-automation` / `office-renderer` / `pdf-render-tool` 导出 PNG/PDF；失败则 `exports.render_status="skipped"` 并写入 `limitations`，不伪造。
6. **自检（≤120s）**：`python scripts/main.py validate --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/work/checks.json`，按结果修订 IR/SVG（≤2 轮）。
7. **收尾（≤120s）**：`python scripts/main.py bundle --ir /app/work/ir.json --meta /app/work/build_meta.json --checks /app/work/checks.json --out /app/output/result.json [--exports x.png x.pdf]`，再 `python scripts/main.py finalize --out /app/output/result.json --status complete|partial`。
剩余时间做增量改进（修正方向、补图例、重渲染、复核 source_ref），每次改动后更新 `change_log`。

## 5. 自检清单（逐条对应评分维度）
- [ ] 完成度：交付可编辑分层 SVG + 真实渲染预览，不是纯文字或单张截图。
- [ ] 正确性：部件名称/数量/接口与材料一致；连接方向正确；箭头无反；数值标注与来源一致。
- [ ] 完整性：六类要素 `entities/relations/data_encoding/legend_annotations/editable_sources/exports` 齐全。
- [ ] 可核验：SVG 含 `data-source-ref`；result.json 含 `sources` 与 `checks`；每个关键结论有 locator + 逐字 quote。
- [ ] 使用增益：IR→布图→自检流水线可复跑，返工与人工修改量下降。
- [ ] 创新增益：沉淀 IR 与布图/编号/图例规则（见 `references/checklist.md`）。
- [ ] 可靠体验：脚本仅标准库、相对路径、确定性布局、异常有降级；重复运行结果一致。
- [ ] 生态贡献：说明、输入约定、图元规范、限制与失败清单齐全。
- [ ] 自检脚本：`python scripts/selftest.py` 退出码 0。

## 6. 降级策略（缺数据/缺软件时如实标注，禁止编造）
- 材料缺失/无法解析：`status=partial`，`limitations`/`uncertainty` 标注"无法判断"，不补造部件、数值或来源。
- 渲染工具缺失：只交付 SVG，`exports.render_status="skipped"`，`limitations` 说明无 PNG/PDF。
- 中文字体缺失（方框）：用 `font-discovery` 查字体；缺中文字体时在 `fonts` 标注并降级为英文标签，不假装嵌入成功。
- 脚本报错：读 stderr；同一策略失败两次即换策略；错误写入 `change_log` 与 `limitations`。
- 坐标/接口冲突：标记 `uncertainty`，遵循原文，不改写科学内容。

## 7. 脚本与资源（scripts/，仅标准库，相对路径）
- `main.py`：`init|build|validate|bundle|finalize` 子命令入口。
- `core_impl.py`：确定性流水线（读入、IR 规范、布图、SVG、校验、结果管理）。
- `selftest.py`：合成样例跑 build+validate 的冒烟自检，退出码 0 表示通过。
- `references/checklist.md`：IR 字段、图元/配色规范、检查清单与失败样例。
