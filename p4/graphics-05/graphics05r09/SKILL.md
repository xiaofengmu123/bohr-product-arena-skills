---
name: graphical-abstract
description: >-
  Produce verifiable, editable, reproducible scientific graphical abstracts from
  /input/case.json and task.md. Extracts entities, relations, data and layout constraints,
  builds a layered text-editable SVG via scripts/main.py + scripts/svg.py, attempts real
  PNG/PDF previews, validates layout and source mapping, and writes /app/output/result.json
  with case_id plus every output_contract field, status, sources, uncertainty, pending_human
  and change_log. Use for graphical-abstract, schematic, mechanism, flowchart, chart-edit.
version: 2.0.0
---

# Graphical Abstract Skill（精简版）

## 1. 角色与何时使用

你是科研图稿作者。给定一个固定案例，产出一页图形摘要（问题→方法→结果→结论）、
一个可编辑分层 SVG 源文件与真实 PNG/PDF 预览，且每个图元都能回溯到来源材料。
适用于 task.md 要求做图形摘要、示意图、机制图、流程图或图表编辑的任务；不写论文
正文、统计或实验设计。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、材料、output_contract）与 `/input/task.md`；
  材料 PDF/CSV/图位于 `/input/` 下，读真实文件、不信任标签。PDF 用平台 pdf-text-tool
  直接解析原文，无预抽取答案层。
- 工作文件在 `/app/work/`，最终产物在 `/app/output/`。Skill 装在 `/skills/weekly-skill/`，
  从该目录运行脚本（相对路径 `scripts/...` 才能解析）。

## 3. 输出契约

必须写 `/app/output/result.json`：含 `case_id` + 题目 `output_contract` 的全部字段
（case 未给出时用 `references/schema.md` 规范模式），再加 `status`、`sources`、
`uncertainty`、`change_log`、`pending_human`。

规范字段：`graphical_abstract`{question,method,results,conclusion}、`editable_source`
{SVG 路径+layers+text_editable}、`exports`{PNG/PDF:renderer+status}、`entities`、
`relations`、`data_mapping`、`legend`、`sources`、`uncertainty`、`limitations`、
`change_log`。`status` 取 `completed | partial | failed`。

可编辑源文件为分层 SVG（`fig_<case_id>.svg`）：`layer-*` 命名组、文字保持可编辑
`<text>`、图元带 `data-source` 属性。导出件为真实渲染的 PNG/PDF；无渲染器时记
`preview_not_rendered`，绝不伪造图片。

## 4. 执行流程（总 900 秒 / 64 次请求）

| 步 | 内容（时间/请求） | 输入 → 输出 |
|-|-|-|
| 0 | **先落盘骨架**（≤90 s，≤4 req） | `main.py init` 解析 `/input/`，写 `/app/work/case_normalized.json`、`figure_spec.json` 模板与 `/app/output/result.json`（status=partial）。**前 3 分钟必须存在合法 result.json** |
| 1 | **抽取事实**（≤120 s，≤14 req） | 读材料，把实体/关系/数据/图面约束写进 `figure_spec.json`，每条带 locator + 逐字 quote |
| 2 | **建图**（≤60 s，≤4 req） | `main.py build` 读 spec → `svg.py` 生成 `fig_<case_id>.svg` + manifest，并尝试 PNG/PDF |
| 3 | **校验溯源**（≤60 s，≤8 req） | `main.py validate` 检查图层/文字/溢出/字号/对比度/来源；修复致命缺陷 |
| 4 | **增量改进**（≤5 min，≤18 req） | 依报告修溢出/小字/低对比/缺来源；重跑 2–3；每次改版追加 `change_log` |
| 5 | **收尾**（≤60 s，≤5 req） | `main.py finalize` 合入 spec+校验+来源，设 status，写 limitations/uncertainty |
| — | 预留（~2 min，~8 req） | 渲染回退、重检、异常处理 |

预算铁律：不超 64 次请求；同一策略失败两次即停，如实记 limitation，继续产出
`partial`，不空手。

## 5. 自检清单（逐条勾选，`scripts/selftest.py` 自动跑部分）

- [ ] result.json 有 case_id + 全部 output_contract 字段 + status/sources/uncertainty/change_log
- [ ] 每个框/箭头/柱/图例色块映射到一条实体/关系/数据，且各有 sources 指针
- [ ] 箭头方向与来源句一致；单位、比例尺、条件标签齐全
- [ ] SVG 分层、命名、文字可编辑、无 text-as-path；PNG/PDF 为真实文件或如实 preview_not_rendered
- [ ] 无画布溢出、字号≥8px、无低对比配色；灰度/色盲分离已查
- [ ] 同输入重复运行结构一致；change_log 随每次重写递增
- [ ] 缺失/歧义项进入 uncertainty/limitations/pending_human，不编造
- [ ] 脚本仅标准库、相对路径调用；无个人文件；`python3 scripts/selftest.py` 退出码 0

## 6. 降级策略（如实标注，不编造）

- 缺数据/缺值：字段设 `null`，加 uncertainty 条目，不估计。
- 实体/方向歧义：拆 id 或用无向连接，注明歧义。
- 缺渲染器：保留 SVG，记 `preview_not_rendered`；再试平台 office-renderer /
  pdf-render-tool / browser-automation；不伪造 PNG/PDF。
- 无 case.json：写 status=failed 骨架并退出（不抛异常），报告阻塞、停止建图。
- 字体替换/导出失败：写入 limitations；可编辑 SVG 仍算有效源文件。
- 脚本找不到 CLI 渲染器时，由你（Agent）用平台工具渲染并把真实文件写回 exports。

## 7. 关键命令

```bash
cd /skills/weekly-skill
python3 scripts/main.py init        # 骨架（先落盘 partial）
python3 scripts/main.py build       # SVG + 预览
python3 scripts/main.py validate    # 校验报告（有 error 时退出码 1）
python3 scripts/main.py finalize    # 最终 result.json
python3 scripts/selftest.py         # 本地自检（空目录/坏 JSON/缺输入各测一次）
```

提交前在非作者环境（不同机器/不同 Python）跑一遍 `selftest.py`。
