---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill：以证据溯源为核心，把论文/汇报稿转成可讲、可核验、可编辑的学术 PPT。先出页级计划（每页一个主张，挂论文图表/章节定位 + 逐字 quote），再在模板母版/版式/主题内成稿并导出页数一致的 PDF。当需要把科研材料整理成可放映、可逐页回溯证据的演示文件时使用。
version: 1.0.0
---

# 论文转学术 PPT（slides-01）· 证据溯源强化版

## 0. 先做这 8 步（照做，别先跑长流程）

1. `mkdir -p /app/output /app/work`；
2. `cat /input/case.json`，记下 `case_id` 与 `output_contract` 的字段名；
3. **立刻**执行 `python3 -B scripts/main.py scaffold --input /input --output /app/output`，
   先落盘一版合法 result.json 骨架（值占位也算可交接交付）；
4. `python3 -B scripts/main.py inspect --input /input` 扫描材料清单；
5. 读材料、抽证据、写 `work/draft.json`，每填一部分就重写一次 result.json；
6. `python3 -B scripts/main.py build-pptx ...` 生成可编辑 pptx（有模板必用 `--template`）；
7. 用平台 office-renderer/pdf-render-tool 导出 PDF，核对页数 == pptx 页数；
8. `python3 -B scripts/main.py finalize ...` 合并，最后补 report.md。

- **时间纪律：900 秒内跑完；每 40 秒至少写一次文件；不要连续思考超过 2 分钟；剩 5 分钟必须已落盘。**
- **预算 64 次模型请求 / 16384 tokens/响应；先交粗稿再改进，不要一遍追求完美。**

## 1. 角色与何时使用

把一篇论文/汇报稿变成**可讲的学术 PPT**：先出页级计划（每页一个主张 + 论文图表/章节定位），
再在指定模板内成稿。不做论文精读结论生成、科研绘图美化、学术润色；相邻课题产物可作输入，
本 Skill 只负责「内容如何变成可讲、可核验、可编辑的演示」。

何时用：材料是论文 PDF / 汇报稿 / 大纲，要求交付学术汇报 pptx + 导出 PDF + 证据映射。
不适用：一次性脚本、纯聊天问答、无材料时的示例填充。

## 2. 输入契约（先发现，再假设）

- `/input/case.json`：`case_id` + `output_contract`（**字段名以它为准**，运行时才可知）。
- `/input/task.md`：任务说明、听众/时长/页数上限、模板要求。
- `/input/` 其余材料：论文 PDF / 汇报稿 / 模板 pptx / 图。**不假设固定文件名**，用 `inspect`
  发现后分类；PDF 用平台 pdf-text-tool/pdftotext 抽真实文本（无预提取答案层）。
- 读取容错：utf-8-sig 优先，坏编码 `errors="replace"`；单文件 >2MB 跳过记 degradations。

## 3. 输出契约（写 /app/output/）

- **`result.json`（必须，缺它 = 0 分）**：回填 `output_contract` 全部键 + `case_id`，再加
  `status` / `sources` / `uncertainty` / `pending_human` / `change_log` / `runtime`；
  本版再加细粒度 `evidence_map`。缺 `output_contract` 时回退七字段
  `case_id,status,editable_source,export_artifact,content_map,compliance_checks,exceptions`。
- `presentation.pptx`（可编辑源）+ `presentation.pdf`（页数必须一致，作渲染预览）+ `report.md`。
- `status`：`ok | partial | insufficient_evidence | input_error`。详见 references/contract.md。

## 4. 执行流程与预算（总 900 秒 / 64 请求，前 3 分钟先落盘）

| 阶段 | 时间 | 请求预算 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架落盘 | 0–3 min | ≤6 | case.json/task.md → **result.json 骨架** + 材料清单 |
| 1 精读抽证 | 3–8 min | ≤24 | 材料 PDF/稿 → `work/draft.json`（页级计划 + evidence_map） |
| 2 成稿导出 | 8–12 min | ≤24 | draft.json + 模板 → pptx + 页数一致 PDF + 预览自检 |
| 3 收尾复核 | 12–15 min | ≤10 | 骨架+产物 → 最终 result.json + report.md |

**前 3 分钟必须已落盘一版 result 骨架（`scaffold`），之后只做增量改进**；每填一部分重写一次
result.json。draft.json 内容（每页主张/证据/取舍）必须由你**真实阅读材料后**写入，脚本不替你
组织内容；读不出写缺口，禁止占位空文件。

## 5. 证据溯源（本版强化点，每条结论都带 locator + 逐字 quote）

`evidence_map` 细粒度结构（完整规范见 references/contract.md）：

```json
{"page_id":3,"role":"result","claim":"一句话主张","numeric_inference":false,
 "evidence":[{"source_id":"materials/paper.pdf","locator":"p7 §3.2 Fig.3",
   "quote":"逐字原文（含数字与单位，未改写）","kind":"figure|table|data|schematic|…",
   "fidelity":"verbatim|paraphrase|numeric_inference|inference","uncertain":false}]}
```

- 每条结论 ≥1 条 evidence；每条 evidence 必有 `source_id` + `locator` + **非空逐字 quote**。
- `kind` 区分实验数据（figure/table/data）与示意图（schematic），防张冠李戴。
- claim 中的数字+单位必须逐字命中 quote，否则标 `numeric_inference=true`（禁静默改写）。
- 冲突两处都记并标 `source_conflict`；缺失写 `uncertainty`/`pending_human`，绝不编造。

## 6. 六个失败模式 → 逐页自检

1. 段落搬家/主线丢失 → 页序按 问题→方法→结果→讨论→结论，每页一个主张；
2. 图表编号错位 → `figure_refs` 与 source 内实际编号逐字一致；
3. 示意图当数据 → `kind=schematic` 显式标注，不作实验证据；
4. 数值单位改写 → quote 逐字含数值单位，否则标 numeric_inference；
5. 备注复读页面 → `speaker_notes` 写过渡/强调/补充，不与正文逐字重复；
6. 模板字体崩坏 → pptx 继承模板 master/layout/theme，PDF 页数 == pptx 页数。

## 7. 自检清单（交付前逐条勾选，完整版见 references/checklist.md）

- [ ] frontmatter 齐备，`scripts/main.py --help` 退出码 0，`selftest.py` 全过；
- [ ] 开跑即 scaffold 落盘 result 骨架，契约字段全占位；
- [ ] 每页 claim/role/evidence 齐全；每条 evidence 有 locator + 逐字 quote；
- [ ] 数值单位逐字命中或 numeric_inference 标记；kind 区分数据/示意图；
- [ ] pptx 真继承模板（有模板时），PDF 页数 == pptx 页数；
- [ ] result.json 回填全部契约键 + 信封字段，status 诚实。

## 8. 降级策略（缺数据/缺软件 → 如实标注，不编造）

- `/input` 缺失/坏 JSON：仍写 result.json，`status="input_error"`、`case_id="unknown-case"`、
  `errors` 记原因（**没有 result.json 才是 0 分**）。
- 材料不足：`status="insufficient_evidence"`，列 `uncertainty`/`pending_human`，不填充示例答案。
- 无 pptx 渲染能力：生成真实 SVG+PNG 替代预览，写 `exceptions`/`degradations`；`editable_source`
  仍指向真实 pptx（本 Skill 用纯标准库 build-pptx 兜底，无 python-pptx 也可产可编辑 pptx）。
- 字体缺失：用 `font-discovery` 查缺，写 `degradations`，不要伪造字体/软件状态/科学数据。

## 9. 脚本用法（scripts/，纯标准库，路径相对 SKILL.md 所在根）

```bash
python3 -B scripts/main.py inspect   --input /input
python3 -B scripts/main.py scaffold  --input /input --output /app/output
python3 -B scripts/main.py validate  --draft work/draft.json
python3 -B scripts/main.py build-pptx --draft work/draft.json \
        --out /app/output/presentation.pptx [--template /input/template.pptx]
python3 -B scripts/main.py pdf-pages --pdf /app/output/presentation.pdf
python3 -B scripts/main.py finalize  --input /input --output /app/output \
        --draft work/draft.json --pptx /app/output/presentation.pptx \
        --pdf  /app/output/presentation.pdf
python3 -B scripts/selftest.py
```

脚本自定位包根，从任意 cwd 均可运行；`core_impl.py` 承载契约发现/证据校验/纯标准库 PPTX 生成。
边界：一个 Skill 只解决「材料 → 学术 PPT」这一类任务，不做大而全的通用生成器。
