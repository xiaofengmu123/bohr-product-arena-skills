---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill：先提取论文主线（问题→方法→证据→讨论），再写页级中间表示 Deck IR（每页一个 claim + role + 证据 locator + 逐字 quote + 图表引用），渲染为真继承模板母版的 pptx 与页数一致的 PDF。适用于把论文/汇报稿/大纲组织成可讲、可核验、可编辑的学术演示。
version: 0.6.0-ir
---

# 论文转学术 PPT（slides-01）

## 0. 角色与何时使用
把固定科研材料（论文 PDF、汇报稿、大纲 + 模板 pptx + 观众/时长/页数约束）组织成**主线成立、页序可讲、证据可追溯**的学术 PPT。何时用：题目要求产出可编辑演示文件 + 页级证据映射 + 渲染/版本记录（slides 赛道）。
**不做**：论文精读结论生成、科研绘图美化、学术写作润色。相邻课题产出可作输入，本 Skill 只负责“论文内容 → 可讲可核验可编辑 PPT”这条链路。

## 1. 输入契约（运行时）
- 权威契约：`/input/case.json` 的 `case_id` + `output_contract`（字段名以它为准，可能缺省）。
- 任务说明：`/input/task.md`；材料：论文/汇报稿/大纲、模板 pptx、图、观众/时长/页数上限。
- 起手两条命令：`cat /input/case.json`；`ls -R /input | head -100`。文本一律按 `utf-8-sig` 读，坏编码/坏 JSON 只降级不崩溃。

## 2. 输出契约（产物写到 `/app/output/`）
必须产出 `/app/output/result.json`，包含：
- `case_id` + `output_contract` 的**全部字段原样回填**（未知键保留、缺值给 `null`/`[]`/`{}`，不改名不删键）；
- `status` ∈ `ok | partial | insufficient_evidence | input_error`，再加 `sources` / `uncertainty` / `pending_human` / `change_log` / `changes` / `errors` / `degradations` / `runtime`。
- `editable_source` / `export_artifact` 指向真实文件：可编辑 `presentation.pptx` + 页数一致的 `presentation.pdf`。无 pptx 能力时给逐页 `SVG` + `PNG` + 最小 PDF，并写 `exceptions`。
- **禁止占位空文件**；页级映射放 `content_map`，逐条自检放 `compliance_checks`。

## 3. 核心方法：Deck IR（页级中间表示）
本 Skill 与“脚本替你组织内容”的旧做法**架构上不同**：内容抽取与渲染解耦。你先把阅读结果写进 `/app/work/deck_ir.json`（渲染无关的中间表示），脚本只做校验+渲染。IR 结构：
- `meta`: `title` / `audience` / `talk_minutes` / `slide_cap` / `template`；
- `spine`: `research_question` / `gap` / `approach_claim` / `key_evidence[]` / `conclusion` / `limitations[]` —— 主线四要素，**不是段落顺序**；
- `figure_index[]`: 每项 `{id, source_id, locator, caption_quote}`，只列材料中真实存在的图/表/公式；
- `slides[]`: 每页 `{index, role, claim, bullets[], figure_ref?, table_ref?, notes{transition,emphasis,q_backup}, evidence[]{source_id,locator,quote}}`。
规则：**一页一个 claim**；`role` 限定 title/background/method/result/discussion/limitation/conclusion/other；`notes` 是过渡+强调+备问，**不重复页面文字**；含数值的 claim 必须带 evidence。

## 4. 执行流程（时间预算：900 秒 / 64 次模型请求 / 16K tokens）
时间纪律：每 40 秒至少写一次文件；不要连续思考超 2 分钟；**前 3 分钟内必须落盘一版 result.json 骨架**，之后只做增量改进。
1. **T0–60s 先落盘**：读 `case.json`，跑 `python3 -B scripts/main.py --skeleton` —— 立即写合法 partial `result.json` + IR 桩，保证任何中断都不空手。
2. **T60–180s 材料全读**：`python3 -B scripts/main.py --scan` 看材料清单；用 `pdftotext` 读论文、直读 md/txt，把全部可读文本拼进 `/app/work/materials.txt`；记下所有图/表/公式编号与图注。
3. **T180–360s 写 IR**：先填 `spine`（问题→缺口→做法→关键证据→结论→局限），再逐页写 `slides` 与 `figure_index`；每项证据写 `source_id + locator + 逐字 quote`。
4. **T360–540s 校验**：`python3 -B scripts/main.py --verify-quotes`（核逐字 quote 命中 materials.txt、核图表引用不张冠李戴），报错就修 IR。
5. **T540–720s 渲染**：`python3 -B scripts/main.py --render` 产出 pptx + PDF；核对 `slides` 数 == PDF 页数；超页/超长就精简（保留主线页）。
6. **T720–860s 组装**：`python3 -B scripts/main.py --assemble` 写最终 `result.json`；跑 `--selftest` 与下方清单。
7. **T860–900s 缓冲**：确认 `result.json` 与两个主产物都在，缺一不可。

## 5. 证据溯源硬规则
- 每个关键结论/数值/图元带 `source_id + locator + quote`，**quote 必须逐字命中材料**（脚本会机器核验，命中失败即 error）。
- 图号/表号与正文引用对得上：`slides[].figure_ref/table_ref` 必须存在于 `figure_index`，且该编号必须在材料文本中出现过（防“引用不存在的图”“张冠李戴”“把示意图当实验数据”）。
- 冲突信息两源都记并标 `source_conflict`；未报告写 `not_reported`；缺材料写 `pending_human`。**绝不编造数值/单位/DOI/图号**。

## 6. 自检清单（交付前逐条勾选）
- [ ] `result.json` 存在且 `case_id`、`status` 合法，`output_contract` 全字段已回填（评分：结果可核验/完整性）
- [ ] 主线四要素齐全，页序按“问题→做法→证据→讨论”，非段落搬家（评分：任务完成度/主线）
- [ ] 每页一个 claim；数值、单位、方法名、结论与论文一致（评分：结果正确性）
- [ ] 每页 evidence 有 locator + 逐字 quote，且 `--verify-quotes` 通过（评分：结果可核验）
- [ ] `figure_ref/table_ref` 均能回溯到真实图/表，无错位/无编造（评分：结果正确性/可核验）
- [ ] 讲解备注含 transition/emphasis/q_backup，且不重复页面文字（评分：讲解逻辑）
- [ ] `presentation.pptx` 真继承模板 master/布局（非“空白+贴字”），`presentation.pdf` 页数一致（评分：模板约束/导出渲染）
- [ ] 缺软件/缺数据已如实写 `exceptions`/`degradations`/`limitations`，未编造（评分：异常处理/诚实状态）
- [ ] `python3 -B scripts/main.py --selftest` 全 PASS（评分：运行稳定）

## 7. 降级策略（缺数据/缺软件）
| 场景 | 处置 |
|---|---|
| `/input` 缺失/坏 JSON | 仍写 `result.json`，`status=input_error`、`case_id=unknown-case`、`errors` 记原因 |
| 材料不足/读不出 | `status=insufficient_evidence`，缺口写 `uncertainty`/`pending_human`，不编造 |
| 无 python-pptx | 逐页 `SVG`+`PNG`+最小 `PDF` 降级，`exceptions` 标注“需 office-renderer 才可得可编辑 pptx” |
| 无 soffice | 用最小文本 PDF 预览，`degradations` 标注“非真实渲染” |
| 公式为图片/PDF 加密/字体缺失/页数超限 | 逐项写 `errors`/`degradations`/`pending_human`，超页优先保留主线页 |

## 8. 脚本接口（相对路径，可独立执行，纯标准库）
| 命令 | 作用 |
|---|---|
| `python3 -B scripts/main.py --skeleton` | 60 秒内落盘合法 partial result.json + IR 桩 |
| `python3 -B scripts/main.py --scan` | 列出材料与契约字段 |
| `python3 -B scripts/main.py --verify-quotes` | 校验 IR + 逐字 quote + 图表交叉引用 |
| `python3 -B scripts/main.py --render` | IR → pptx（模板继承）+ PDF（页数一致） |
| `python3 -B scripts/main.py --assemble` | 组装最终 result.json（content_map/compliance_checks/change_log） |
| `python3 -B scripts/main.py --selftest` | 本地自检：空目录/坏 JSON/IR/引用/PDF/frontmatter |

模块划分：`core.py`（契约+IR+证据+校验+记账，无渲染）、`build.py`（pptx/SVG/PNG/PDF 渲染+页数核验）、`main.py`（薄编排+自检）。详见 `references/deck-ir-spec.md` 与 `references/checklist.md`。
