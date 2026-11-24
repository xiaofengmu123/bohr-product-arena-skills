---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill——契约优先地把论文/研究材料组织成可讲、可核验、可编辑的学术演示。先在 3 分钟内落盘 result.json 骨架（逐一对齐 /input/case.json 的 output_contract 全部字段），再读材料产出页级计划与证据映射，继承模板母版/版式生成真实 pptx 与页数一致的 PDF。适用：给定论文 + 模板 + 听众/时长/页数约束，产出可放映、可回溯、可编辑的学术汇报。
version: 1.0.0
---

# 论文转学术 PPT（slides-01）

## 1. 角色与何时使用

把一篇论文或研究材料转成**可讲、可核验、可编辑**的学术 PPT：先提取主线（问题 →
方法 → 证据 → 结论 → 限制），再落成页级主张（一页一个 claim），每页挂论文的
图/表/章节证据，最后继承给定模板生成真实 pptx 与页数一致的 PDF。当输入是论文 +
模板 + 听众/时长/页数约束、输出要能放映并复核时使用本 Skill。不负责论文精读结论、
科研绘图美化、学术润色。

## 2. 硬预算与第一优先（务必照做）

- 总预算：**900 秒 / 64 次模型请求 / 单响应 16384 tokens**。
- **前 3 分钟只做一件事**：落盘 `/app/output/result.json` 骨架（§4），保证任何中断
  都留下合法可交接的结果；之后每 40 秒至少重写一次 result.json 增量，剩余 5 分钟
  必须已定稿落盘。先交粗稿再改进，不追求一遍完美。

## 3. 输入契约

- 权威契约：`/input/case.json`（`case_id` + `output_contract`，字段名以它为准）。
- 任务说明：`/input/task.md`；材料：论文/汇报稿/大纲、模板 pptx、图表、听众/时长/页数约束。
- 起手：`cat /input/case.json`；`ls -R /input | head -100`；PDF 用 pdftotext 提取文本，
  不依赖"已预提取答案层"。读实际文件，不按标签假设内容。

## 4. 输出契约（核心：逐一对齐 output_contract）

先写 `/app/output/result.json`，再写产物：

```json
{
  "case_id": "<copy from /input/case.json>",
  "editable_source": {"path": "presentation.pptx", "format": "pptx", "slides": 0},
  "export_artifact": {"pdf": {"path": "presentation.pdf", "pages": 0}},
  "content_map": [],
  "compliance_checks": [],
  "exceptions": [],
  "status": "partial",
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [],
  "errors": [],
  "degradations": [],
  "runtime": {}
}
```

- **全部 `output_contract` 字段必须原样回填**：不改名、不删未知键；未知键先给
  `null`/`[]`/`{}`，无法匹配产物时在 `change_log` 记录 `action=default`，不臆造。
- `status` 只取 `ok | partial | insufficient_evidence | input_error`，诚实定档。
- 统一信封必含 `status / sources / uncertainty / pending_human / change_log`，
  另加 `errors / degradations / runtime`。
- 产物：真实可编辑 `presentation.pptx` + 页数一致 `presentation.pdf`（+ 必要 PNG 预览）；
  无 pptx 能力时给真实 SVG+PNG 并写 `exceptions`。

## 5. 执行流程（分步、每步输入输出、时间预算）

| 步 | 做什么 | 输入 → 输出 | 预算 |
|-|-|-|-|
| P0 | 建目录，读 case.json，**落盘骨架** | case.json → result.json(partial) | ≤3 分钟，先落盘 |
| P1 | 读论文提取主线，写 `work/plan.json` | 材料 → narrative + pages | 3–7 分钟 |
| P2 | 继承模板生成 pptx + 导出 pdf | plan + 模板 → pptx/pdf | 7–12 分钟 |
| P3 | 合并校验定稿，写 report.md | plan + 产物 → result.json(ok/partial) | 12–15 分钟 |

`work/plan.json` 页级结构（脚本据此合并，不替你写内容）：

```json
{
  "deck_title": "…", "audience": "…", "time_min": 20, "slide_cap": 12,
  "narrative": ["问题", "方法", "证据", "结论", "限制"],
  "pages": [
    {"slide_no": 1, "role": "title|background|method|result|discussion|limitation|conclusion|other",
     "claim": "一页一个主张", "bullets": ["…"], "figure_refs": ["Fig. 2"],
     "layout": "模板中的版式名", "speaker_notes": "讲解备注，不重复页面文字",
     "evidence": [{"source_id": "paper.pdf", "locator": "paper.md#p4", "quote": "逐字命中原文"}]}
  ],
  "sources": [], "uncertainty": [], "pending_human": [],
  "field_assignments": {"<契约字段>": "editable_source|export_artifact|content_map|compliance_checks|exceptions|null"}
}
```

P1 主线提取四步（落到 `narrative`，再做页级拆解）：

1. **一句话问题**：本文要解决什么（gap/任务），不写背景套话。
2. **核心做法**：方法/系统名 + 关键设计选择，区分主结论与支撑性细节。
3. **证据链**：按图/表/公式排证据，标真实编号；示意图≠实验数据，数值/单位不改写。
4. **结论与限制**：本文自认的边界与未覆盖场景；他人结果不写成本文结果。

脚本调用（相对路径，均纯标准库）：

```bash
python3 scripts/main.py --skeleton-only                       # P0 骨架
python3 scripts/main.py --print-contract                       # 打印契约字段名
python3 scripts/main.py --plan work/plan.json --check-quotes   # P3 合并校验定稿
python3 scripts/pptx_util.py presentation.pptx presentation.pdf  # 体检页数/版式/主题
python3 scripts/selftest.py                                    # 提交前自检
```

## 6. 模板继承与产物规则

- **必须真继承模板**：打开给定模板/起始 deck，编辑占位符，保留母版(slideMasters)、
  版式(slideLayouts)、主题(theme)；禁止"新建空白 + 贴文本框"伪继承。
- 长标题折行、多图并列、大表格：用模板版式排布，不硬塞导致溢出；字号层级遵循模板。
- 导出 PDF 后核对页数 == 幻灯片数；换机/换字体风险记录到 `runtime`（实际字体）。
- 每页内容回填 `content_map`，证据 `source_id + locator + quote` 齐全，quote 逐字命中。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] `result.json`：case_id + 全部契约字段 + 信封（含 change_log），字段逐一对齐。
- [ ] 主线 narrative 与论文贡献一致，不是段落顺序搬运。
- [ ] 每页一个 claim，有 role + evidence；图表编号与正文引用对得上、单位/数值未改写。
- [ ] 讲解备注与页面文字不重复，写过渡/强调点/被问补充证据。
- [ ] pptx 合法（`[Content_Types].xml` + slide≥1）且含 layouts/masters/theme。
- [ ] pdf 以 `%PDF-` 开头且页数 == 幻灯片数；页数不超上限。
- [ ] 空目录/坏 JSON/缺 case.json 三场景：退出码 0、写 input_error 骨架、不抛异常。
- [ ] `python3 scripts/selftest.py` 退出码 0；无占位空文件、无编造引用/分数。

## 8. 降级策略（诚实标注 limitations，不编造）

- 缺数据/坏 JSON → `input_error`，原因入 `errors`；缺材料 → `insufficient_evidence` +
  `pending_human` 缺口清单。
- 缺 python-pptx/LibreOffice → 用 `pptx_util` 只做校验，降级 SVG+PNG 并写 `exceptions`，
  不声称生成了 pptx。
- PDF 加密/图表无法提取/公式为图片 → 标 `not_parsed`/`not_reported`，写 `uncertainty`。
- 模板占位符不足/字体缺失/页数超限 → 少页或取舍说明写 `exceptions`/`change_log`。
- 任何材料读不出的数值、单位、结论：写 `pending_human`，**绝不编造**。

## 9. 边界与禁止

- 脚本只做契约/校验/合并，**不替你组织内容**：claim/evidence/取舍由你真实读材料写进
  `work/plan.json`；读不出写缺口，不写示例答案。
- 不提交预先写好的正式答案、白板或伪造分数；`examples/` 不设正式案例答案。
