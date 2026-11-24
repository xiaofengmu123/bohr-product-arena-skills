---
name: graphical-abstract
description: >-
  从 /input/case.json 与 /input/task.md 的固定科研案例提取实体、关系、数据与图面约束，
  生成“研究问题—方法—核心结果—结论”的单页分层可编辑 SVG，并渲染真实 PNG/PDF 预览；
  对每个图元做 locator+逐字 quote 证据溯源，按评分维度生成 rubric_trace，并把 case_id、
  全部 output_contract 字段与 status/sources/uncertainty/pending_human/change_log 写入
  /app/output/result.json。适用于图形摘要、示意图、机制图、流程图与图表编辑任务。
version: 1.1.0
---

# Graphical Abstract Skill（评分维度对齐版）

## 1. 角色与适用场景

你是科研图稿作者。给定一个固定科研案例，产出**单页图形摘要**（研究问题→方法→核心结果→结论），
同时交付**可编辑分层源文件**与**真实渲染预览**，且图中每个图元都能回到来源材料逐项核对。
当 `task.md` 要求图形摘要、示意图、机制图、流程图或图表编辑时使用本 Skill。

本 Skill 不负责论文正文写作、统计分析与实验方案设计，只对图形中的科学内容表达、版式、
可编辑性与导出质量负责。判断是否做对，不看图是否精美，而看三件事：每个实体/关系/数值能否
追到来源材料；同样输入能否稳定复现结构一致的图稿；交付的是不是可继续编辑的源文件而非位图。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、材料清单、`output_contract`）与 `/input/task.md`
  （任务文本）；材料 PDF/CSV/图件也可能在 `/input/` 下。**必须读实际文件，不得凭标签补造**；
  PDF 文本由 PDF 自身解析而来，无预提取答案层，需先用 `pdftotext` 导出到 `/app/work` 再定位。
- 工作文件写 `/app/work/`，最终产物写 `/app/output/`。
- Skill 安装在 `/skills/weekly-skill/`；从该目录运行脚本（`cd /skills/weekly-skill`），
  脚本内相对路径 `scripts/*.py` 才能解析。

## 3. 输出契约

必须写 `/app/output/result.json`，包含：

1. `case_id`（取 case.json 的 `id`/`case_id` 或 task.md 的 challenge_id）；
2. 题目 `output_contract` 的**全部字段**——缺值给 `null`/`[]`，不改名、不删未知键；题目未给时
   使用 `references/evidence_contract.md` 定义的标准 schema；
3. 附加字段：`status`（严格取 `completed|partial|failed`）、`sources`、`uncertainty`、
   `pending_human`、`change_log`。

可编辑源文件为分层 SVG `fig_<case_id>.svg`：命名组 `layer-*`，文字保留 `<text>`，图元带
`data-source` 属性；导出件为真实 PNG/PDF，无渲染器时写 `preview_not_rendered`，**绝不伪造图像**。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬性规则：前 3 分钟内必须先落盘一版 `result.json` 骨架（`status=partial`）。** 之后只做增量
改进；每步改写都要在 `change_log` 追加一条记录。任一步骤连续两次同策略失败即停止重试，如实记
`limitations` 并继续。全程 ≤64 次模型请求，超出预算前优先交 `partial` 而非缺件。

| # | 步骤（时间 / 请求） | 输入 → 输出 |
|-|-|-|
| 0 | 触发与定向（≤30 s / ≤2 req） | 读 `task.md` 判断任务类型，读本 SKILL.md 与两份 references，确定目标 |
| 1 | 规范化并落盘骨架（≤60 s / ≤6 req） | `python scripts/main.py --stage extract` → `/app/work/case_normalized.json`、`figure_spec.json` 模板、`/app/output/result.json`（partial 骨架） |
| 2 | 抽取事实与证据（≤150 s / ≤14 req） | 读材料（PDF 先 pdftotext），把实体/关系/数据/约束/证据逐条写入 `/app/work/figure_spec.json`；每个关键项带 `locator`+逐字 `quote` |
| 3 | 构建图稿（≤60 s / ≤4 req） | `python scripts/main.py --stage build` → `fig_<case_id>.svg` + 尝试 PNG/PDF + `/app/work/fig_manifest.json` |
| 4 | 校验与溯源（≤60 s / ≤8 req） | `python scripts/main.py --stage validate` → `/app/work/validation_report.json` + `rubric_trace.json`；修正方向/映射/溢出等关键缺陷 |
| 5 | 增量改进（≤6 min / ≤20 req） | 按校验报告修溢出/字号/对比度/缺源/方向，重跑步骤 3–4；每次重写 bump `change_log` |
| 6 | 定稿（≤60 s / ≤4 req） | `python scripts/main.py --stage finalize` → 设 `status`，写 `limitations`/`uncertainty`，最终 `result.json` |
| 7 | 降级兜底（预留 ~2 min / ~6 req） | 缺数据/缺软件时按第 6 节处理，交 `partial`/`failed` 而非缺件 |

## 5. 自检清单（每条对应评分维度，逐条勾选）

编号 S1–S18 与 `references/rubric_alignment.md` 的映射表一一对应；`scripts/selftest.py`
与 `--stage validate` 自动检查其中多项。最终交付前逐条勾选。

- [ ] S1 `result.json` 含 `graphical_abstract` 四要素且 SVG 有四面板（任务完成度）。
- [ ] S2 每个 box/arrow/bar/legend swatch 映射到一个 entity/relation/data 项（结果正确性）。
- [ ] S3 箭头方向与来源句因果一致；单位、数值、比例尺标签齐全（结果正确性）。
- [ ] S4 图例颜色/长度与数据编码一一对应，色觉安全调色板（结果正确性）。
- [ ] S5 `case_id` + 全部 `output_contract` 字段 + 附加字段齐全，缺值给 `null`/`[]`（结果完整性）。
- [ ] S6 每个关键结论/数值带 `locator` + 逐字 `quote`，`verified` 状态如实（结果可核验性）。
- [ ] S7 SVG 分层、命名、`<text>` 可编辑；PNG/PDF 为真实文件（质量增益）。
- [ ] S8 一次 `build` 同时产出源文件与预览，无跨软件搬运（效率增益）。
- [ ] S9 `rubric_trace.json` 已生成并入 `result.json`；`change_log` 每步留痕（增益可验证性）。
- [ ] S10 `layer-panels/entities/relations/data/labels/legend/annotations` 七层齐全（新增能力）。
- [ ] S11 布局确定性：固定网格、稳定排序、固定调色板（方法改进）。
- [ ] S12 `python scripts/selftest.py` 通过；同一输入两次构建字节一致（创新证据）。
- [ ] S13 frontmatter `name/description/version` 齐备，脚本相对路径可执行（调用易用性）。
- [ ] S14 相同 `figure_spec.json` 复跑结构一致，无随机因素（运行稳定性）。
- [ ] S15 缺失/歧义项在 `uncertainty`/`pending_human`/`limitations`，未编造（异常处理）。
- [ ] S16 SKILL.md + 两份 references 完整、与提交版本一致（文档完整性）。
- [ ] S17 脚本仅标准库、无隐藏个人文件；`figure_spec.json` 模板可复用（资源可复用性）。
- [ ] S18 `GITHUB_URL.txt` 有效，图稿案例/版式经验可沉淀（分享推广价值）。

## 6. 降级策略与诚实标注

- **缺数据/缺数值**：字段置 `null`，加 `uncertainty` 条目；不估计、不插值、不编 DOI/数值。
- **实体/方向歧义**：拆分 id 或用 `undirected` 连接，并在 `uncertainty` 注明。
- **缺渲染器/字体**：保留 SVG，`exports.status=preview_not_rendered`，试 rsvg/inkscape/
  magick/chromium 与平台 office-renderer、pdf-render-tool、browser-automation；绝不伪造 PNG/PDF。
- **无 case.json**：`extract` 写 `status=failed` 骨架 + `limitations`，停止后续步骤并报告阻塞。
- **文字溢出/字号过小/低对比度/画布超限**：由 `validate` 报出，删减要点或并入图注后重跑。
- **字体替换/导出失败**：记 `limitations`；可编辑 SVG 仍算有效交付。

## 7. 脚本与资源清单

- `scripts/main.py`：流程编排（extract/build/validate/finalize），标准库，`--help` 退出码 0。
- `scripts/core_impl.py`：规范化、确定性 SVG 构建、证据提取、校验与 rubric_trace 的纯函数。
- `scripts/selftest.py`：自检（空输入、坏 JSON、确定性、契约、端到端 dry-run）。
- `references/rubric_alignment.md`：评分维度 → 流程步骤 → 自检项映射（本版核心取舍）。
- `references/evidence_contract.md`：证据溯源规则、`figure_spec.json` 与 `result.json` schema。
