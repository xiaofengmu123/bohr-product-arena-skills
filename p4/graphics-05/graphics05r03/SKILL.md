---
name: graphical-abstract
description: >-
  Produce verifiable, editable, reproducible scientific graphical abstracts from the
  official /input/case.json and task.md. Deterministic pipeline: extract entities,
  relations, data and constraints with locator + verbatim-quote evidence, build a layered
  text-editable SVG via scripts/main.py + scripts/core_impl.py, render real PNG/PDF
  previews, validate and backfill sources, and write /app/output/result.json with case_id,
  every output_contract field, status, sources, uncertainty, pending_human and change_log.
  Same input yields byte-identical result.json and SVG; scripts/selftest.py verifies
  determinism and degradation paths. Use for graphical-abstract, schematic, mechanism,
  flowchart and chart-edit tasks.
version: 1.0.0
---

# Graphical Abstract Skill（确定性版）

## 1. 角色与何时使用

你是科研图稿作者。给定一个固定科研案例，产出一页**图形摘要**（研究问题→方法→核心结果→结论）、一份**可编辑分层源文件**和**真实渲染预览**，且每个图元都能溯源到原始材料。适用于任何要求图形摘要、示意图、机制图、流程图或图表编辑的任务。不写论文正文、不做统计分析或实验设计。

本版的核心取舍是**自检与确定性**：同一输入两次运行，`result.json` 与 `fig_*.svg` 字节一致；`scripts/selftest.py` 可在无作者环境验证这一点。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、材料、`output_contract`）与 `/input/task.md`（题面）。材料 PDF/CSV/图件也可能在 `/input/` 下；**读取真实文件，不信任文件名或领域标签**。PDF 文本来自 PDF 本身，无预抽取答案层。
- 工作文件放 `/app/work/`；最终产物放 `/app/output/`（含 `result.json`）。
- Skill 安装在 `/skills/weekly-skill/`。脚本用相对路径 `scripts/core_impl.py`，运行目录为 `/skills/weekly-skill`。

## 3. 输出契约

必须写 `/app/output/result.json`，包含：

- `case_id` **加上** case 中 `output_contract` 的**全部字段**（未知键原样保留，缺值给 `null`/`[]`，不改名不删除），再加：
- `status` ∈ `completed | partial | failed`；`graphical_abstract` {question, method, results, conclusion}
- `editable_source` {format, path, layers[], text_editable}；`exports` [{format, path, renderer, status}]
- `entities` / `relations` / `data_mapping` / `legend` / `sources` / `uncertainty` / `pending_human` / `change_log` / `limitations` / `errors` / `degradations` / `determinism`

**路径规则**：`result.json` 中所有 path 相对 `/app/output` 解析（可移植、可复现）。
**证据规则**：每个 entity/relation/data/legend 条目带 `source`（locator）+ `quote`（逐字引用）；`sources[]` 汇总唯一 locator。追不到来源的数值写 `null` 并进 `uncertainty`，**禁止编造**。
**状态规则**：有实体且无 error 缺陷 → `completed`；有 critical → `failed`；否则 → `partial`。

可编辑源文件是分层 SVG `fig_<case_id>.svg`：命名 `layer-*` 组，文字保持 `<text>` 可编辑，图元带 `data-source`/`data-id`。导出件是真实 PNG/PDF；无渲染器时记 `preview_not_rendered`，绝不伪造位图。

## 4. 确定性设计

1. 所有 JSON 用 `sort_keys=True` 写盘；result.json 是输入的纯函数——不含墙钟时间、进程号、随机数。
2. 实体/关系/数据 ID 与布局仅由稳定排序派生，不引入 PRNG；颜色取固定色板顺序。
3. `determinism.fingerprint` 是科学内容（abstract/entities/relations/data/legend/sources）的 SHA-256 前 16 位；运行时遥测单独写 `/app/work/run_meta.json`，不进 result.json。
4. `python -B scripts/selftest.py` 用合成 demo 输入验证：两次运行 result.json 与 SVG 字节一致、契约字段完整、空目录/坏 JSON 降级不崩溃。

## 5. 执行流程（900 s / 64 次模型请求）

**硬性规则：前 3 分钟内必须把合法的 `result.json`（status=partial）骨架写到 `/app/output/`。此后只做增量改进。**

| # | 步骤（时间, 请求） | 输入 → 输出 |
|-|-|-|
| 1 | **落盘骨架**（≤3 min, ≤8 req） | `/input/case.json`+`task.md` → `python scripts/main.py extract`；产出 `case_normalized.json`、`figure_spec.json` 模板、`result.json`（partial） |
| 2 | **科学抽取**（≤3 min, ≤18 req） | 材料 → 人工/Agent 填写 `figure_spec.json` 的 entities/relations/data/constraints/legend/evidence，每条带 locator+逐字 quote |
| 3 | **建图**（≤1 min, ≤4 req） | `figure_spec.json` → `python scripts/main.py build`；产出 `fig_*.svg` + `fig_manifest.json` + PNG/PDF 尝试 |
| 4 | **校验溯源**（≤1 min, ≤6 req） | SVG+spec+result → `python scripts/main.py validate`；回填 sources/data_mapping/legend，写 `validation_report.json` |
| 5 | **增量改进**（≤5 min, ≤20 req） | 检查缺陷 → 修 spec → 重跑 build/validate；每次改写 `change_log` 递增 |
| 6 | **定稿**（≤1 min, ≤4 req） | `python scripts/main.py finalize` → 设 status、算 fingerprint、写 limitations/uncertainty |
| — | **预留**（~1 min, ~4 req） | 异常处理、渲染器回退、复查 |

请求上限 64，超限前停止。任一步骤卡住（缺文件、实体歧义、导出失败）同一策略失败**两次**即停止重试，如实记录 limitation，以 `partial` 收尾。

## 6. 自检清单（对应评分维度）

- [ ] result.json 含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/pending_human/change_log（结果完整性/可核验性）。
- [ ] 每个 entity/relation/data/legend 有 `source` locator + 逐字 `quote`；`sources[]` 汇总唯一 locator，无虚构 DOI/数值（证据溯源）。
- [ ] 箭头方向与原文因果一致（activates/inhibits/leads to 对应 from→to）；数据编码与数值+单位一一对应；图例 swatch 颜色与 data/entity 编码一致（结果正确性）。
- [ ] SVG 分层命名、text 可编辑、无 text-as-path；PNG/PDF 是真实文件（可编辑源文件/导出件）。
- [ ] 无画布溢出、字号 ≥8 px、对比度达标、色觉/灰度可分离（版式与视觉层级）。
- [ ] 同输入两次运行 result.json 与 SVG 字节一致；fingerprint 稳定；`selftest.py` 4 项通过（运行稳定性/可复核）。
- [ ] 缺数值 → null+uncertainty；歧义 → 拆分/无向连接并注明；缺 case.json/坏 JSON → 合法 failed/partial 骨架不崩溃（异常处理）。
- [ ] 脚本相对路径 `scripts/*.py`、纯标准库、main.py < 30KB；references 讲清 schema 与清单（生态贡献/复用性）。

## 7. 降级策略

- **缺数据/数值**：字段写 `null`，加 `uncertainty`，不估算、不编造。
- **实体/方向歧义**：拆分 id 或用无向连接器，注明歧义。
- **缺渲染器/软件**：保留 SVG，记 `preview_not_rendered`，尝试平台 office-renderer / pdf-render-tool / browser-automation；绝不伪造 PNG。
- **无 case.json**：`extract` 写 `status=failed` 骨架并退出码 1，报告阻塞，停止依赖步骤。
- **字体替换/导出失败**：写入 `limitations`；可编辑 SVG 仍计为有效源文件。
- **坏 JSON/坏编码**：记录到 `errors`/`degradations`，继续处理其余材料。

## 8. 文件清单与复用

```
SKILL.md                     # 本文件：契约、流程、清单、降级
scripts/main.py              # 编排入口：extract/build/validate/finalize/run（<30KB）
scripts/core_impl.py         # 确定性抽取、布局、SVG 构建、校验、渲染（纯标准库）
scripts/selftest.py          # 自检：确定性 + 契约 + 空目录 + 坏 JSON
references/output_schema.md  # result.json 字段与证据规则
references/checklist.md      # 可勾选清单与评分维度映射
```

最小调用示例（标注为示例，非正式案例结果）：

```bash
cd /skills/weekly-skill
python -B scripts/main.py extract
# 编辑 /app/work/figure_spec.json（填入 entities/relations/data/evidence）
python -B scripts/main.py build && python -B scripts/main.py validate
python -B scripts/main.py finalize
python -B scripts/selftest.py   # 提交前自检
```
