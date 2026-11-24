---
name: figure-earlydrop-skill
description: 从科研材料（装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分）抽取实体、连接、步骤与材料/能量/信息流向，构建结构化中间表示（IR），按 900 秒/64 请求的阶段预算在 180 秒内先落盘 result.json 部分骨架，再增量生成可编辑、分层、可溯源 SVG 图稿并完成渲染、自检与收尾。适用于需要"画得对、可编辑、可复跑"的实验装置与技术路线绘图任务。
version: 2.0.0
---

# 科研绘图 Skill：装置图 / 流程图 / 机制图 / 技术路线图（早落盘版）

## 1. 角色与何时使用

本 Skill 让 Agent 先把材料抽取为「实体—连接—步骤—流向」结构化中间表示（IR），再据此确定布图、生成可编辑分层 SVG、自检、渲染与导出。它解决的是"画得像却不对"：部件名称对不上、管路凭空补齐、箭头反向、步骤编号错位、图例与图形不一致、只交一张栅格截图、字体缺失变方框。

- **使用时机**：输入含装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分，需产出装置图、机制图、流程图或技术路线图。
- **不适用**：数据分析与数值拟合；判断实验方案本身是否科学合理；论文排版与期刊投稿格式适配。

## 2. 输入契约（运行时）

- 原始输入固定为 `/input/case.json`（案例说明、输入约束、可能声明的 `output_contract`）与 `/input/task.md`（题面）。
- 科研材料与图件按 `case.json` 指引读取，**先发现再假设**：不依据文件名或标签猜测内容。PDF 用平台 `pdf-text-tool` / `pdf-render-tool` / `ocr-tool` 解析实际文件；图表数据以实际源数据为准。可用 `python scripts/main.py discover --input /input` 扫描输入目录（大小、BOM、JSON 合法性、PDF 魔数）。
- 工作目录 `/app/work`；最终产物写到 `/app/output/`；脚本位于本 Skill 根 `scripts/`，先 `cd /skills/weekly-skill` 后用相对路径调用，如 `python scripts/build_svg.py ...`。
- 可用工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8）。若 `case.json` 声明了 `output_contract`，按其字段名**原样回填全部字段**（缺值给 `null`/`[]`，不改名、不删未知键），再补下列通用字段；未声明时采用默认契约字段。

- 默认契约字段（对应六类要素）：
  - `entities`：科学实体（id、label、type、source_ref、quote、note、x/y/w/h 可选）。
  - `relations`：空间/流向关系（id、from、to、kind、direction、style、source_ref、quote）。
  - `data_encoding`：数据映射（key、value、visual、source_ref、quote）。
  - `legend_annotations`：图例与标注映射（key、visual、label、source_ref、quote）。
  - `editable_sources`：可编辑源文件清单（path、format、layers、notes）。
  - `exports`：导出件清单（path、format、width、height、dpi、fonts、color_check、render_status）。
- 通用补充字段：`case_id`、`status`（`in_progress | complete | partial | failed`）、`title`、`started_at`、`ended_at`、`budget`、`phase`、`sources`、`uncertainty`、`pending_human`、`limitations`、`checks`、`artifacts`、`change_log`。
- 关键约定：可编辑源文件优先交付**分层 SVG**；另交真实渲染的 PNG/PDF 预览；SVG 中每个实体/关系/图例/标注均带 `data-source-ref`，`result.json` 中对应项带 `locator + quote` 可逐字回溯。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**硬性要求**：读完 `case.json` 后立即用 `python scripts/main.py init ...` 落盘 result.json 骨架（status=in_progress、case_id、全部空契约字段、budget、首条 change_log），**deadline 为第 180 秒**；此后只做 `merge`/`finalize` 增量改进。任何时刻中断都留下可解析的诚实部分结果。每阶段给出"输入→输出与时间预算"：

| 阶段 | 动作（输入→输出） | 秒 | 请求 | 累计 |
|-|-|-:|-:|-:|
| p1 read_input | 读 case.json + task.md，`discover` 扫描材料 → 确定 case_id、图种 kind、材料路径、output_contract | ≤60 | 6 | ≤60s |
| p2 drop_skeleton | `main.py init` → `/app/output/result.json` 骨架 | ≤60 | 2 | **≤120s（早于 180s）** |
| p3 extract_ir | 抽取 entities/relations/steps/stages 并逐条附 source_ref+quote → `main.py merge` 写入 IR | ≤180 | 16 | ≤300s |
| p4 build_svg | `build_svg.py ir.json figure.svg` → 分层 SVG → `merge` editable_sources | ≤180 | 12 | ≤480s |
| p5 render | 渲染工具导出 PNG/PDF → `merge` exports | ≤120 | 8 | ≤600s |
| p6 selftest | `selftest.py` → checks.json → `merge` checks，按结果修订（≤2 轮） | ≤120 | 10 | ≤720s |
| p7 finalize | 复制产物到 `/app/output/`，`main.py finalize` 写 status/ended_at/artifacts | ≤180 | 10 | ≤900s |

每次 `merge` 都更新时间戳并追加 `change_log`；剩余时间用于修正连接方向、补图例、重渲染、复核 source_ref。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] **完成度**：交付可编辑分层 SVG + 真实渲染预览，不是纯文字或单张截图。
- [ ] **正确性**：部件名称/数量/接口与材料一致；连接方向正确；无反向/断头箭头；数值标注与来源一致。
- [ ] **完整性**：entities / relations / data_encoding / legend_annotations / editable_sources / exports 六类齐全。
- [ ] **可核验**：SVG 含 `data-source-ref`；result.json 含 sources 与 checks；每项带 locator+逐字 quote。
- [ ] **使用增益**：IR→布图→自检流水线可复用，返工与人工修改量下降。
- [ ] **创新增益**：沉淀 IR 中间表示与布图/编号/图例规则（见 `references/ir_schema.md`）。
- [ ] **可靠体验**：脚本仅标准库、相对路径、异常降级；重复运行结果一致（确定性布局）。
- [ ] **生态贡献**：说明、输入约定、图元规范、限制与失败清单齐全。
- [ ] **预算**：p2 骨架在 180s 内落盘；每阶段未超时；`selftest.py --smoke` 通过。

## 6. 降级策略（缺数据/缺软件如实标注 limitations，禁止编造）

- 材料缺失/无法解析：在 `limitations` 与 `uncertainty` 标注"无法判断"，不补造部件、数值或来源。
- 渲染工具缺失：只交 SVG，`exports.render_status="skipped"`，`limitations` 说明无 PNG/PDF。
- 字体缺失（中文方框）：用 `font-discovery` 查字体；缺中文字体时在 `fonts` 标注并降级为英文标签，不假装嵌入成功。
- 脚本报错：读 stderr 定位；同一策略失败两次即换策略，错误写入 `change_log` 与 `limitations`。
- 坐标/接口冲突：标记 `uncertainty`，优先遵循原始材料原文，不改写科学内容。
- 坏 JSON/坏编码：`main.py` 按 utf-8-sig/utf-8/errors=replace 容错，写入 `pending_human` 或 `errors`，继续处理其余材料。

## 7. 脚本与资源（相对路径，纯标准库）

- `scripts/main.py`：`discover`（输入发现）/ `init`（早落盘骨架）/ `merge`（增量合并）/ `finalize`（收尾与产物登记）。
- `scripts/build_svg.py`：IR → 分层可编辑 SVG（图层、箭头、图例、标注、source_ref 元数据，确定性布局）。
- `scripts/selftest.py`：SVG/IR 自检（断头箭头、方向、图例一致、画布溢出、source_ref/quote 覆盖、quote 与源材料比对）；`--smoke` 自举验证。
- `references/ir_schema.md`：IR 字段、图元、布图、图例规范。
- `references/checklist.md`：可勾选检查清单、常见失败样例与降级登记表。

最小调用示例（示意，勿冒充正式案例）：`python scripts/main.py init --case /input/case.json --out /app/output/result.json`，随后 `python scripts/selftest.py --smoke` 验证脚本自举可跑。
