---
name: academic-talk-organizer
description: 把论文或研究材料组织成证据可追溯、可编辑、符合模板的学术汇报：报告目标、听众定位、页级唯一主张、图表引用、讲解顺序、讲稿备注、问答准备与渲染检查。用于从已有科研材料到可上台、可复核演示文件的组织与交付；不负责论文精读、学术写作成稿或科研绘图素材生产。
version: 1.0.0
---

# 学术汇报内容组织 Skill

## 1. 角色与触发条件

本 Skill 把「已有研究材料」组织成「可讲的页面」。任一条件满足即使用：

- 任务要求把论文/研究材料做成演示文稿，且需逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、模板等约束，需做内容取舍与叙事顺序设计；
- 评分要求证据可追溯、导出可打开、结论强度与证据匹配。

不适用：论文精读、学术写作成稿、科研绘图素材生产。

核心方法：先定报告目标与听众，再定「问题—方法—结果—结论」主线与页级取舍；每页一条唯一主张 + 支撑证据 + 证据强度，配套讲稿与问答；最后逐页回填来源定位并检查导出渲染。

## 2. 输入契约

运行时原始输入（只读，勿改）：

- `/input/case.json`：案例标识、任务约束、材料清单；若含 `output_contract`，其全部字段为输出契约基准，须原样保留并回填。
- `/input/task.md`：任务说明。
- 材料文件按 `case.json` 清单定位，通常位于 `/input/` 或 `/app/work/`；PDF 以实际内容为准，不依标签臆测。

可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。

工作目录 `/app/work/`；最终产物写入 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，含：

- `case_id`：优先 `case.json.id`，其次 `armiId`；
- `output_contract` 的全部字段（缺值给 `null`/`[]`，不改名、不删键）；
- 附 `status`、`sources`、`uncertainty`、`change_log`。

无 `output_contract` 时使用默认字段：`report_goal`、`audience`、`narrative`、`pages`、`editable_deck`、`evidence_map`、`qa_prep`、`rendering`。字段定义见 `references/contract.md`。

`pages` 每页含：`page_index`、`title`、`single_claim`（唯一主张）、`evidence_strength`（high/medium/low）、`figures`、`speaker_notes`、`source_refs`（每条含 `locator` + 逐字 `quote`）。`evidence_map` 逐条含 `claim`、`locator`、`quote`、`strength`。

可编辑演示文件（PPTX/HTML/ODP）与渲染预览（PDF/PNG）一并写入 `/app/output/`；未产出的字段留空或显式 `null`，不得编造。

## 4. 执行流程（总预算 900 秒 / 64 次请求）

规则：每步先落盘后改进；**前 3 分钟（180 秒）内必须写出 `result.json` 骨架**（步骤 1–2），此后只做增量改进；到 850 秒即收尾。

| 步骤 | 输入 | 输出 | 预算 |
|-|-|-|-|
| 1 读取核对 | case.json、task.md、材料 | 材料清单、约束、output_contract | ≤60s，3–5 请求 |
| 2 落盘骨架 | 步骤 1 的 case_id/契约 | `scripts/main.py scaffold` 生成 partial | ≤120s，1–2 请求 |
| 3 主线规划 | 材料全文 | report_goal / audience / narrative / pages 初稿 | 120–300s，8–15 请求 |
| 4 生成演示件 | pages + 模板 | 可编辑 deck + speaker_notes + 导出 | 300–540s，10–20 请求 |
| 5 证据映射 | pages + 来源定位 | evidence_map，跑 `scripts/main.py evidence` | 540–660s，5–10 请求 |
| 6 自检收尾 | 自检报告 | 修复缺陷、更新 change_log，跑 `scripts/main.py validate` | 660–900s，余量 |

每步用 `scripts/main.py scaffold --update` 把新字段合并进 `result.json`，保证骨架始终最新、已有内容不丢。请求数为上限，不追求用完。

## 5. 内容组织规则（可执行启发式）

主张写作：

- `single_claim` 是一句可上台讲出的结论，宜 ≤40 字，动词为主，不含引用编号与括号堆叠；
- 一个页面只留一个主张，其余信息进 `speaker_notes` 或删去；
- 先把段落压成主张，再决定上页与否，不得把摘要/Results 段落逐句搬页。

证据强度：有对照实验且统计显著 → `high`；单组观察/相关分析/案例 → `medium`；机制推测/文献引用/未复现 → `low`；缺出处或无法定位 → `low` 并记入 `uncertainty`。

图表绑定：

- 每页 `figures` 只放直接支撑本页主张的图/表，一个图不上两页重复出现；
- 图缺失时标「图缺失/不可用」，不生成伪造图。

叙事顺序：

- 主线固定为「问题—方法—结果—结论」，但页数按听众重配；
- 组会：方法与进度前置；答辩：创新点与边界前置；评审：结论与证据强度前置；
- 每个取舍（某图不上页、某节后移）都要在 `narrative` 写一句理由。

问答预演：

- `qa_prep` 覆盖四类追问：机制、数据、对照、局限；
- 每条含「问题 + 口径一致的答案要点 + 可引用页面」，不写与页面矛盾的备用口径。

## 6. 自检清单

逐条可勾选，完整映射见 `references/checklist.md`。

- [ ] 报告目标、听众、叙事顺序、逐页主张、问答准备形成闭环（对应 40 分维度）。
- [ ] 每页恰好一条 `single_claim`，无摘要/Results 逐段搬页。
- [ ] 每页数字、图表引用与源材料一致；`evidence_strength` 与证据强度匹配。
- [ ] 覆盖论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染六方面。
- [ ] `evidence_map` 每条有 `locator` + 逐字 `quote`，`scripts/main.py evidence` 通过。
- [ ] 取舍决策写入 `narrative` 或 `change_log`，可复核。
- [ ] 可编辑文件 + PDF/PNG 预览均存在，`scripts/main.py validate` 无 error。
- [ ] 缺图/低分辨率/公式丢失/字体缺失/导出失败/超时均如实标注。

## 7. 降级策略

- 材料缺失或无法定位来源：保留 `single_claim`，`evidence_strength` 标 `low`，记入 `uncertainty`，不补造出处。
- 图表缺失或低分辨率：页面注明「图缺失/不可用」，记入 `rendering`，不生成伪造图。
- 字体/模板/office-renderer 不可用：退回纯文本大纲或 HTML deck，`status` 标 `partial`，说明降级原因。
- 导出失败或超时：保留可编辑源文件与部分结果，`status` 如实标 `partial`/`failed`，记入 `change_log`。
- 缺失软件状态或科学数据只标 limitations，禁止编造。

## 8. 脚本调用（仅标准库，相对路径）

在 SKILL.md 所在目录执行：

```bash
python3 scripts/main.py scaffold --case /input/case.json --task /input/task.md --out /app/output
python3 scripts/main.py evidence --result /app/output/result.json --sources /app/work/sources.json
python3 scripts/main.py validate --result /app/output/result.json --case /input/case.json
python3 scripts/selftest.py
```

均支持 `--help`；`sources.json` 为实际读取来源清单（path/type/read），可省略则只做结构校验。字段定义见 `references/contract.md`，评分对应清单见 `references/checklist.md`。

## 9. 最小调用示例（示意，非正式案例结果）

输入 `case.json`（示意）：`{"id":"case-demo","output_contract":{"report_goal":"让评审认可方法新颖性","audience":"评审委员","pages":[],"evidence_map":[]}}`

调用：`python3 scripts/main.py scaffold --case case.json --task task.md --out ./demo/output`

示意输出 `result.json`：`{"case_id":"case-demo","status":"partial","sources":[],"uncertainty":[],"change_log":[…]}`，并含 `output_contract` 全部字段。

## 10. 常见失败模式与处置

| 失败模式 | 识别信号 | 处置 |
|-|-|-|
| 摘要搬页 | 多页 `single_claim` 与摘要逐句相同 | 每页重写为唯一主张，删冗余页 |
| 页数与时长脱节 | 页数 × 90 秒 > 时长 | 按听众重配，砍到可讲完 |
| 图表与主张不对应 | `figures` 不支撑本页 claim | 换图或删图引用 |
| 引用编号错位 | 导出后公式/编号与源文不一致 | 逐页回填 locator 核验 |
| 字体/母版不合规 | 渲染 PDF 字体回退、版式错乱 | 记录 rendering，换模板或降级 HTML |

## 11. 边界与可复现

- 一个 Skill 只解决「材料到可讲页面」这一件事，不扩展成通用 PPT 引擎；同一输入重复运行应产出确定性结果，随机布局须固定种子。
- 空输入/坏 JSON 不得崩溃：写出合法 `input_error`/`insufficient_evidence` 结果并记录原因。
