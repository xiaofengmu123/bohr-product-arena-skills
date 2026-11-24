---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill：把论文/研究材料组织成可讲、可核验、可编辑的学术 PPT——先出页级计划（每页一个主张 + 图表/章节定位 + 讲解备注），再成稿。要求每页 claim/role/evidence 带 locator 与逐字 quote；交付真可编辑 pptx + 页数一致 PDF + 契约字段齐全的 result.json。
version: 1.0.0-lean
---

# 论文转学术 PPT（slides-01）

## 1. 角色与何时使用

把一篇论文或研究材料转成**可讲的学术汇报**，不是把段落切块贴进页面。核心转换是
一次结构重写：提取问题—方法—证据—结论的主线，逐页写成「一个主张 + 一张图/表/段落
证据 + 一条讲稿」，并在给定模板里排得下、打开能放映、换机能复核版式。

**何时用**：拿到论文 PDF/全文/大纲 + 模板 pptx + 听众/时长/页数约束，需要交付可编辑
演示文件时。**不负责**：论文精读结论生成、科研绘图美化、学术写作润色。判断对错只看
三件事——主线是否与论文自身贡献一致、每页能否回溯到图/表/章节/数据、成品能否打开
编辑并由他人复跑出同一版式。

## 2. 输入契约

运行时固定路径（先发现，再假设，不硬编码文件名）：

- 权威契约：`/input/case.json`（`case_id` + `output_contract`，字段名以它为准）
- 任务说明：`/input/task.md`
- 材料：论文/汇报稿/模板 pptx/图表等，用 `ls -R /input | head -100` 起手发现
- 工作区 `/app/work`，**最终产物写到 `/app/output/`**

第一步必做：`mkdir -p /app/output`，读 `case.json` 记下 `case_id` 与 `output_contract`
的全部字段名；`output_contract` 可能是 dict / list[str] / list[dict] 三种形态，脚本
`scaffold` 会自动解析。

## 3. 输出契约

`/app/output/result.json` 必须含 `case_id` + **output_contract 的全部原始字段**（缺值给
`null`/`[]`/`{}`，不改名、不删未知键），再加统一信封：

| 字段 | 内容 |
|---|---|
| `case_id` | 从 case.json 复制 |
| `status` | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| 主产物 | 题目契约字段指向真实文件，如 `editable_source.path="presentation.pptx"` |
| `sources` | 材料清单（rel/bytes/kind） |
| `uncertainty` | 未报告/读不出的内容，绝不编造 |
| `pending_human` | 需人工确认项 |
| `errors` / `degradations` | 失败与降级记录 |
| `change_log` | 每次落盘的增量记录（at/action/note） |
| `runtime` | 起止时间、模式、耗时 |

产物：真实 `presentation.pptx` + 页数一致的 `presentation.pdf` + `plan.json`
（页级计划）+ `notes.md`（讲稿，逐页对应、不重复页面文字）。**禁止占位空文件。**

## 4. 执行流程（900 秒 / 64 请求 / 每响应 16384 tokens）

按步推进，**每步完成即重写一次 result.json**，保持可交接；连续思考不超过 2 分钟，
剩 5 分钟必须已落盘。脚本只作单次工具，不串成流水线。

| 步骤 | 做什么 | 产物 | 预算 |
|---|---|---|---|
| P0（前 3 分钟） | 读 case.json→`python3 -B scripts/main.py scaffold` 落盘 partial 骨架，再读材料列主线候选 | result.json 骨架 + work/plan.json | 3 分钟 / ≤8 请求 |
| P1 | 读论文全文/图表，抽取问题-方法-结果-限制，写页级计划（每页 claim/role/evidence+locator+quote/figures/notes） | work/plan.json | 每 40 秒 checkpoint |
| P2 | `python3 -B scripts/main.py build` 生成 pptx（自动继承 /input 下 .pptx 模板；无模板降级最小 deck） | presentation.pptx | — |
| P3 | 用 office-renderer 导出 PDF，`stats` 核对页数=slides 数 | presentation.pdf | — |
| P4 | 补 notes.md、report.md，`check` 校验契约与文件，回填 result.json | 最终交付 | 剩 5 分钟前完成 |

P0 之后只做**增量改进**：每填一页重写 plan.json；每阶段边界把真实进展合并进 result.json
并追加 `change_log`，永不推倒重来。任何中途异常都不会留下不完整产物。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py scaffold          # 读契约 → 写合法 partial 骨架
python3 -B scripts/main.py build             # plan.json → presentation.pptx（模板真继承）
python3 -B scripts/main.py check             # 校验 result.json 契约 + 主产物文件
python3 -B scripts/main.py stats             # 只读检查 pptx/pdf 结构（不改文件）
python3 -B scripts/selftest.py               # 提交前自检（退出码 0 = 全部通过）
```

`build` 先跑证据校验：任何页缺 `locator` 或逐字 `quote` 都**拒绝生成**（退出码 2），
防止把无溯源页面写进 pptx。模板继承是「复制模板包 + 追加 slide 复用其 layout/master/
theme」，不是空白页贴文本框。

## 6. 自检清单（交付前逐条勾选）

- [ ] frontmatter 齐备（name/description/version），SKILL 可被 Harness 加载
- [ ] result.json 含 case_id + output_contract 全部字段 + 信封（status/sources/uncertainty/change_log）
- [ ] P0 骨架在前 3 分钟落盘；change_log 有增量记录
- [ ] 每页 claim/role/evidence，evidence 有 source_id + locator + 逐字 quote
- [ ] claim 中数字/单位/方法名逐字命中 quote；示意图与实验数据未混淆
- [ ] pptx 可打开可编辑；有模板时真继承（stats 显示 masters/layouts 非空）
- [ ] PDF 页数 = slides 数；notes.md 与页面对应且不重复页面文字
- [ ] 缺数据/缺软件已写 limitations，无编造数值或结论
- [ ] `python3 -B scripts/selftest.py` 全部 PASS；文件数 5 个、无占位空文件

## 7. 降级策略（缺什么标注什么）

- `/input` 缺失/坏 JSON：仍写 result.json，`status="input_error"`、`case_id="unknown-case"`，
  `errors` 记录原因——**没有 result.json 才是 0 分**。
- PDF 加密/图表不可提取/公式为图片：该页 evidence 标 `not_extractable` 并写入 `uncertainty`。
- 无模板或无可用版式：`build` 降级为最小合法 deck，`render_record.changes` 记录。
- 无 office-renderer 导出 PDF：交付 SVG/PNG 预览，`export_pdf=null`，`exceptions` 如实写。
- 材料未报告某结论：写 `pending_human`，绝不补造。
