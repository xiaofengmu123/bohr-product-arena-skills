---
name: graphical-abstract
description: >-
  从 /input/case.json 与 task.md 中抽取科学实体、关系、数据与图面约束，先产出
  FigureIR 中间表示（fig_ir.json），再由确定性网格布局引擎生成分层可编辑 SVG 与
  fig_ir.json/fig_manifest.json，尝试渲染真实 PNG/PDF，经 scripts/selftest.py 与
  validate 校验后写 /app/output/result.json（case_id + 全部 output_contract 字段 +
  status/sources/uncertainty/pending_human/change_log/limitations）。适用于
  graphical-abstract、示意图、机制图、流程图与图表编辑任务。
version: 1.1.0
---

# Graphical Abstract Skill

## 1. 角色与何时使用

你是一名科研图稿作者：给定一个固定科研案例，产出一页“研究问题→方法→核心结果→结论”的
图形摘要，并交付可继续编辑的分层源文件与真实渲染预览。每一条实体、关系与数值都能经
`locator + quote` 指回来源材料；同一输入两次运行得到结构一致的图稿。

当 `task.md` 要求 graphical abstract、示意图、机制图、流程图或图表编辑时使用本 Skill。
不写论文正文、不设计实验、不做统计分析；科学判断留在抽取与改进阶段，机械的版式、绘制、
导出与校验交给脚本。**架构取舍（差异化）**：抽取与构建之间引入显式、版本化的
FigureIR 中间表示，版面由确定性网格引擎生成，校验作用在真实 SVG 产物并回查 IR，三者
职责分离，避免“抽取—绘制—校验”耦合成一个脚本。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例事实、材料、`output_contract`）与
  `/input/task.md`（任务文本）。材料 PDF/CSV/图件也可能位于 `/input/`，必须读真实文件，
  不以文件名/标签代替内容；PDF 文本来自 PDF 本身，没有预抽取答案层。
- 工作文件写 `/app/work/`，最终产物写 `/app/output/`（含 `result.json`）。
- Skill 安装在 `/skills/weekly-skill/`。运行脚本时使用相对路径
  `python3 -B scripts/core_impl.py`，或以 `/skills/weekly-skill` 为工作目录调用；`main.py`
  按脚本所在目录解析 `core_impl` 导入、`selftest.py` 以 `__file__` 锚定自身目录，
  不依赖作者个人文件。
- `main.py` 缺省参数：`--input /input --output /app/output --work /app/work`。

## 3. 输出契约（result.json）

必须写 `/app/output/result.json`，包含 `case_id` **加上**案例 `output_contract` 中的
**全部字段**；若案例未给契约，则用 `references/output_schema.md` 的规范 schema。此外
**必须**附加：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、
`limitations`、`runtime`、`artifacts`。`status` 仅取
`completed | partial | failed`。

主产物为分层 SVG `fig_<case_id>.svg`：`layer-*` 具名分组、`<text>` 保持可编辑、
关键图元带 `data-source` 属性；导出件为真实渲染的 PNG/PDF（无渲染器时写
`preview_not_rendered`，绝不伪造位图）。中间表示 `fig_ir.json` 与构建清单
`fig_manifest.json` 同写输出目录，支撑复核与重跑。

`output_contract` 中的未知键原样保留：缺值给 `null`/`[]`，不改名、不删键。

## 4. 执行流程（900 s / 64 请求，前 3 分钟先落盘骨架）

顺序执行，每步列出输入→输出。**规则：前 3 分钟内 `result.json` 合法骨架必须已在
`/app/output/` 上（status=partial），此后只做增量改进；两次失败即停用同一策略。**

| # | 步骤（时间/请求） | 输入 → 输出 |
|-|-|-|
| 1 | 归一化与落骨架（≤60 s，≤6 req） | `/input/case.json`+`task.md` → `python3 -B scripts/main.py`（先跑一轮）→ 写出 `/app/work/case_normalized.json`、`fig_ir.json` 模板、`result.json` 骨架（partial） |
| 2 | 抽取事实并填充 FigureIR（≤120 s，≤14 req） | 归一化案例 + PDF/CSV/OCR → 写入实体/关系/数据/面板/图面约束与 evidence（locator+quote）；随后 `python3 -B scripts/main.py --only extract` 落盘 `fig_ir.json` |
| 3 | 确定性构建（≤60 s，≤6 req） | `fig_ir.json` → `python3 -B scripts/main.py --only build` → 生成 `fig_<case_id>.svg`、`fig_manifest.json`，并尝试 PNG/PDF |
| 4 | 校验与溯源（≤60 s，≤8 req） | SVG+IR+清单 → `python3 -B scripts/main.py --only validate` → 补齐 `sources`/`data_mapping`/`legend`，修关键缺陷后重跑 3–4 |
| 5 | 增量改进（≤360 s，≤18 req） | 校验报告 → 修溢出/小字号/低对比/缺来源/方向反，每轮改 `change_log` |
| 6 | 收尾（≤60 s，≤6 req） | 最终 SVG/PNG/PDF+报告 → 定 `status`、写 `limitations`/`uncertainty`/`pending_human` |
| — | 预留（≈120 s，≈6 req） | 异常处理、渲染器回退、复核 |

第 2 步的科学抽取是 LLM 判断：从材料文本中判断实体、关系方向、数据编码与图注；脚本只
负责结构化的中间表示、确定性版式与机械校验，二者职责分离。不要凭领域标签补造数据。

## 5. 自检清单（逐条勾选，`scripts/selftest.py` 与 `--only validate` 自动执行多项）

- [ ] `result.json` 含 `case_id` + 全部 output_contract 字段 + status/sources/uncertainty/pending_human/change_log/limitations/runtime（结果完整性/可核验性）。
- [ ] 每个框/箭头/柱条/图例色块映射到一条 entity/relation/data 记录，且该记录带 `locator`+逐字 `quote`（实体关系与数据映射正确）。
- [ ] 箭头方向与来源句一致；单位、比例尺、条件标签齐全（结果正确性）。
- [ ] SVG 分层、具名、`<text>` 可编辑、无 text-as-path；PNG/PDF 为真实文件或如实 `preview_not_rendered`（可编辑源文件/导出件）。
- [ ] 无画布溢出、无字号 < 8 px、无低对比配色；色觉/灰度分离已核（版式与视觉层级）。
- [ ] 同一输入复现同一结构；`change_log` 每次重写 +1（运行稳定性/可复核）。
- [ ] 缺失/歧义项进 `uncertainty`/`limitations`/`pending_human`，绝不编造（异常处理）。
- [ ] 脚本纯标准库、以相对路径 `scripts/*.py` 运行、无隐藏个人文件（生态贡献/复用性）。

## 6. 证据溯源（每个关键结论硬要求）

- evidence 条目形如 `{"id","locator","quote","kind"}`：`locator` 是材料内定位
  （如 `materials/paper.md#p3` 或 `case.json#entities[0]`），`quote` 是来源中的逐字片段。
- 图中出现的每个数值、方向、颜色编码都要能回溯；冲突信息两条都记并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 7. 降级与诚实策略

- **缺数据/缺值**：字段写 `null`，加 `uncertainty` 条目；不做估算。
- **实体或方向歧义**：拆分 id 或用无向连接器，并在 `uncertainty` 注明。
- **缺渲染器/软件**：保留 SVG 源文件，写 `preview_not_rendered`，依次尝试平台
  office-renderer / pdf-render-tool / browser-automation；绝不伪造 PNG。
- **无 case.json**：写 `status=failed` 的最小合法结果并记录 limitation，停止后续步骤。
- **坏 JSON/坏编码/超限文件**：按 `utf-8-sig` 容错、跳过并记 `degradations`；单文件
  >2MB 跳过。
- **字体替换/导出失败**：写入 `limitations`，可编辑 SVG 仍计为主产物。

## 8. 目录与运行

```text
SKILL.md
scripts/main.py        # 流水线编排：extract/build/validate 子命令，默认全流程
scripts/core_impl.py   # FigureIR 中间表示、归一化、确定性布局、SVG 构建与校验、渲染探测
scripts/selftest.py    # 自检：空输入/坏 JSON/合成案例三态 + SVG 可解析
references/output_schema.md   # result.json 规范 schema 与 FigureIR 字段
```

```bash
python3 -B scripts/main.py --help                 # 退出码 0
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

产物结构一致、可复核、可编辑；本包只含 Skill 与脚本，不含预制答案或正式测试产物。
