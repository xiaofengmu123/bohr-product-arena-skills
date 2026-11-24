---
name: scix-figure-evidence
description: 从科研材料（装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分）抽取实体、连接、步骤与物料/能量/信息流向，构建"元素级 locator + 逐字 quote"的结构化中间表示（IR）与更细的 evidence_map（正向/反向双索引），据此布图生成可编辑分层 SVG，完成渲染与自检，最终交付可核验的 /app/output/result.json。适用于"画得对、可溯源、可编辑、可复跑"的实验装置与技术路线绘图任务。变体：证据溯源强化。
version: 1.1.0
---

# scix-figure-evidence：证据溯源的科研装置与技术路线绘图

## 1. 角色与何时使用
先构建"实体—连接—步骤—流向"结构化 IR，再布图、生成可编辑 SVG、自检与导出，并把每个科学元素追到输入材料的逐字原文（`source_ref` + `quote`），最后聚合为 `evidence_map`（正向索引 + 反向索引）。避免只输出文字或单张截图、凭空补管路、箭头反向、图例与图形不一致、无溯源。
- **使用时机**：输入含装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分，需产出装置图、机制图、流程图或技术路线图。
- **不适用**：数据分析与数值拟合；判断实验方案本身是否科学合理；论文排版与期刊投稿格式适配。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json`（案例说明、输入约束、output_contract 如声明）、`/input/task.md`（题面）。
- 科研材料：按 `case.json` 指引读取；PDF 用 `pdf-text-tool` / `pdf-render-tool` / `ocr-tool` 解析实际文件，不依据标签假设内容，并把正文写入 `/app/work/materials_text/*.txt` 供脚本读取；图表数据以实际源数据为准。
- 工作目录 `/app/work`；最终产物写到 `/app/output/`（含 `result.json`）。
- 可用工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。
- 脚本位于本 Skill 根目录 `scripts/`，先 `cd /skills/weekly-skill`，用相对路径调用（如 `python scripts/main.py run ...`）。

## 3. 输出契约
必须产出 `/app/output/result.json`（UTF-8 JSON）：
- `case_id`：取自 `/input/case.json`（优先级 `case_id > id > challenge_id > armiId`）。
- 若 `case.json` 声明了 `output_contract`，**原样输出全部字段名**（缺值给 `null`，不改名、不删未知键）；未声明时用默认六类：`entities`、`relations`、`data_encoding`、`legend_annotations`、`editable_sources`、`exports`。
- 通用字段（评分要求）：`status`（`complete|partial|insufficient_evidence|failed`）、`title`、`primary_artifact`、`sources`、`evidence_map`、`uncertainty`、`pending_human`、`change_log`、`checks`、`limitations`、`artifacts`、`runtime`。
- **证据溯源（本变体核心）**：`result.json` 内的 `evidence_map` 为更细结构，含 `elements`（正向：元素→locator+逐字 quote+confidence+method）、`reverse_index`（反向：来源→元素列表）、`coverage.quote_coverage` 与 `unquoted`（未溯源项显式列出）。每条结论都带 `locator` 与逐字 `quote`。
- 关键约定：可编辑源文件优先交付**分层 SVG**；另交付真实渲染 PNG/PDF；SVG 每个实体/关系/图例/标注带 `data-source-ref` + `data-quote` + `data-evidence-id`，并嵌入完整 `scix-evidence-map`。

## 4. 执行流程（总预算 900 秒、64 次模型请求，16384 tokens/响应）
**硬性要求**：前 3 分钟（约 180 秒）内先落盘 `result.json` 骨架（`status=in_progress`，含 case_id、空契约字段、首条 change_log），之后只做增量改进；任何时刻可交付诚实部分结果。

1. **读输入（≤90s）**：读 `/input/case.json` + `task.md` → 确定 case_id、图种 kind、材料路径、output_contract 字段。
2. **落盘骨架（≤60s，累计须 <180s）**：`python scripts/main.py init` → 生成 `/app/output/result.json`（in_progress）。
3. **提取 IR（≤150s）**：解析材料，抽取 entities/relations/steps/stages，逐条附 `source_ref`+逐字 `quote`；写 `/app/work/ir.json`（字段见 `references/ir_schema.md`）。脚本兜底 `python scripts/main.py extract` 输出低置信 IR，模型据此校正补 quote。
4. **布图生成 SVG（≤180s）**：`python scripts/main.py build` → IR→分层 SVG；坐标缺失时脚本网格/链式布图并在 `layout.auto=true` 标注，模型可微调 `layout.positions` 重跑。
5. **渲染预览（≤120s）**：`python scripts/main.py render` 探测本地渲染器；或 `browser-automation`/`office-renderer` 导 PNG/PDF；失败写 `limitations`，不编造。
6. **自检（≤120s）**：`python scripts/main.py validate` 写 `/app/work/checks.json`；按结果修订 IR/SVG（最多 2 轮）。
7. **收尾（≤120s）**：`python scripts/main.py merge` 汇合 entities/relations/sources/evidence_map/uncertainty/checks，`python scripts/main.py finalize --status complete|partial` 写状态与结束时间。

剩余时间用于增量改进（补 quote、修正方向、补图例、重渲染、复核 source_ref），每次改动更新 `change_log`。自检脚本：`python scripts/selftest.py`（空输入、坏 JSON、正常输入三情形）。

## 5. 自检清单（逐条对应评分维度）
- [ ] 完成度：可编辑分层 SVG + 真实渲染预览，非纯文字/单张截图。
- [ ] 正确性：部件名称/数量/接口一致；连接方向正确；箭头无反；数值与来源一致。
- [ ] 完整性：六类契约字段齐全。
- [ ] 可核验：SVG 含 `data-source-ref`/`data-quote`；result.json 含 `sources`、`evidence_map`、`checks`；`quote_coverage` 已写入。
- [ ] 使用增益：IR→布图→自检流水线可复用，返工与人工修改量下降。
- [ ] 创新增益：沉淀元素级 evidence_map（正向/反向索引）与布图/编号/图例规则。
- [ ] 可靠体验：脚本仅标准库、相对路径、异常有降级；重复运行一致。
- [ ] 生态贡献：说明、输入约定、图元规范、限制与失败清单齐全。

## 6. 降级策略（缺数据/缺软件如实标注 limitations，禁止编造）
- 材料缺失或无法解析：`limitations` 与 `uncertainty` 标"无法判断"，`quote=null`、`method="derived"`，不得补造部件/数值/来源。
- 渲染工具缺失：只交付 SVG，`exports.render_status="skipped"`，`limitations` 说明无 PNG/PDF。
- 字体缺失（中文方框）：`font-discovery` 查字体；缺中文字体时在 `exports.fonts` 标注并降级英文标签。
- 脚本报错：读 stderr 定位；同一策略失败两次即换策略，写入 `change_log` 与 `limitations`。
- 坐标/接口冲突：标 `uncertainty`，优先遵循材料原文，不改写科学内容。
- `output_contract` 含未知字段：输出该字段名并置 `null`，不改名、不删。

## 7. 脚本与资源（相对路径，标准库）
- `scripts/main.py`：`init/extract/build/validate/render/merge/finalize/run` 子命令。
- `scripts/core_impl.py`：IR 抽取、evidence_map、SVG 构建、校验、结果组装（保持 main.py 精简）。
- `scripts/selftest.py`：契约/证据/SVG 三情形自检。
- `references/ir_schema.md`：IR 字段、evidence_map 结构与图元规范。
- `references/checklist.md`：可勾选清单与常见失败样例。
