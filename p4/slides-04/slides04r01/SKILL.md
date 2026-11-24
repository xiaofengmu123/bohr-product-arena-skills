---
name: academic-slides-evidence
description: 把科研论文/材料套入指定 PPT 模板，产出可打开、可继续编辑的 pptx、逐页证据映射（每条结论带 locator+逐字 quote）与可复核的渲染/版本记录；用于“学术 PPT 模板与格式适配”类任务，覆盖模板套用、版式约束、字体颜色、图表适配、批量一致性与导出渲染六类要素，并以“证据溯源强化”为突破方向。
version: 1.0.0
---

# 学术 PPT 模板与格式适配（证据溯源强化版）

## 1. 角色与何时使用

你是“学术汇报制作器”：给定一篇论文/科研材料和一个带母版/版式的模板，交付一份**可打开、可继续编辑、证据可追溯、渲染可复核**的学术演示文稿，而不是“像 PPT 的粘贴板”。

**何时使用**：任务输入是 `/input/case.json` + `/input/task.md`，且要求生成 PPTX 并保留证据映射与渲染记录时启用。不要用预制答案；每个案例从实际输入文件现做。

**核心判据**（决定成败，不看页数）：① 模板是否被真实套用（母版/版式/占位符继承关系可查）；② 页内文字、数字、图号与源材料是否一致，**每条结论能否指回材料的 locator + 逐字 quote**；③ 整册版式与导出件能否被他人复跑逐页核对。

**本版侧重点——证据溯源强化**：`evidence_map` 采用 schema v2，粒度比“只给 refs”更细——每个结论为 claim 级（type/status），其下每条证据为 evidence 级（locator/quote/verbatim/confidence/matcher/score）。逐字 quote 只取材料原文，未定位到则 `pending_human`，绝不编造。

## 2. 输入契约

- 原始输入在 `/input/case.json`（含 `case_id`、材料路径、约束、可能的 `output_contract`）与 `/input/task.md`。
- 论文/模板/图表按题面路径提供；PDF 以**原文件**给出，无预提取答案层。**读真实文件**，不得按标签脑补内容。
- 工作区 `/app/work/`（提取文本、中间 JSON、页计划），最终产物写 `/app/output/`。
- 可用工具见 `environment/resources.yaml`（pdf-text-tool、pdf-render-tool、ocr-tool、office-renderer、font-discovery、scientific-python 等）；本 Skill 的三个脚本只用 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` + 题目 output_contract 全部字段 + `status / sources / uncertainty / pending_human / change_log`。三个契约字段：

- `presentation`：可编辑演示文件 —— `pptx_path / pdf_path / png_paths / slide_count / page_plan[]`（每页 `{index,title,claim,layout,notes,refs}`）。
- `evidence_map`：页级证据映射（schema v2）—— 每页 `{page_index,page_title,claims[]}`，每条 claim 含 `{claim_id,claim,type,status,evidence[]}`，每条 evidence 含 `{locator{source,page?,line_start,line_end},quote,verbatim,confidence,matcher,score}`。
- `render_record`：渲染与版本记录 —— `source_files / template_file / layouts_used / fonts / exports[] / integrity / media[] / checks[]`。

字段详义见 `references/output_contract.md`。可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览。若 `case.json` 自带 `output_contract`，以其为准并补齐上述字段。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应≤16384 token）

**硬规则：前 3 分钟内必须用 `scripts/main.py --phase init` 落盘一版结果骨架**，之后只做增量改进；任何一步失败都保留已落盘的部分结果。

| 阶段 | 时间 | 动作（输入→输出） |
|---|---|---|
| 0 引导 | 0–3 min | 读 `/input/case.json`+`task.md`，清点文件；跑 `python scripts/main.py --phase init --input /input --output /app/output --work /app/work` → 落 `result.json`（含契约骨架）+ `work/input_inventory.json` |
| 1 提取 | 3–8 min | 用 pdf-text-tool/pdftotext 提取论文为 `/app/work/extracted/*.txt`；OCR 处理扫描页；font-discovery 查字体 |
| 2 页计划 | 5–10 min | 从主线提炼研究问题/方法/结果/限制，设计页级主张（每条带 type）、图引用、讲解顺序与版式 → 写 `work/page_plan.json` |
| 3 证据映射 | 8–11 min | 跑 `python scripts/main.py --phase evidence …` → 生成 locator+逐字 quote 的 `work/evidence_map.json` 并入 result |
| 4 制作 | 11–13 min | 用 office-renderer 按模板真实套用：选对 layout/占位符，写正文+讲稿备注，图表等比缩放，统一字号间距阶梯 → `/app/output/*.pptx` 并导出 PDF/PNG |
| 5 校验收口 | 13–15 min | 跑 `python scripts/main.py --phase validate … --pptx <f> --pdf <f> --png <f…>` 再 `--phase finalize` → 修溢出/字体/图号，最多 2 轮，最终写 `result.json` |

脚本路径相对 Skill 根目录调用，参数用 `--help` 查看。请求预算纪律：文本只提取一次并缓存到 `/app/work`，每步 1–2 次请求，留余量给返工。

## 5. 自检清单（逐条勾选，对应评分维度）

- 提交前跑 `python scripts/selftest.py`：空输入/坏 JSON/合成案例/幂等四类健壮性自动验证，退出码 0 表示通过。
- 逐页完成后按 `references/checklist.md` 勾选；未满足项写入 `uncertainty`，证据缺口写 `pending_human`，不假装通过。
- 确定性检查（结构/魔数/定位/溢出启发式）交给脚本；视觉套用、数值语义、逐页渲染一致只能人工核，边界在 checklist 中标明。

## 6. 降级策略（诚实 > 完整）

- **缺输入文件/缺模板版式/缺字体/图表源失效/导出失败**：在 `uncertainty` 逐条写 `{code,page?,detail}`，`status` 降为 `partial` 或 `failed`，`change_log` 记原因与时间戳。
- 模板缺所需 layout：用最接近的 layout 显式命名并记入 `render_record.layouts_used` 的替代关系，不得谎称“已继承”。
- 字体缺失：用 font-discovery 查可用字体，记录回退字体到 `render_record.fonts`，禁止假装字体存在。
- 证据未定位：结论写 `pending_human`，`quote` 留空，不编造逐字片段；多来源数值冲突标 `source_conflict` 并保留两个来源。
- 图源失效或导出件无法生成：如实保留 `null` 路径并说明，**不伪造文件、不编造运行分数或软件状态**。
- 部分结果也要完整落盘：init 阶段就写好所有字段（空值/null），后续只填充。

## 7. 脚本与资源

- `scripts/main.py`：分阶段编排（init/evidence/validate/finalize），幂等合并，任何异常都尽力落盘部分结果；`--help` 查看参数。
- `scripts/core_impl.py`：共享实现——稳健 IO、输入发现、证据定位与逐字 quote、pptx 结构检查、溢出启发式、预览魔数检查。
- `scripts/selftest.py`：自检（空输入/坏 JSON/合成最小案例/幂等），纯临时目录运行，不产生正式测试产物。
- `references/output_contract.md`：result.json 全字段说明；`references/checklist.md`：自检清单（自动/人工边界）。

## 8. 边界

- 不做“通用大而全”：一个 Skill 只解决“论文+模板→pptx+PDF+证据映射”一类任务。
- 没有真实材料时输出 `insufficient_evidence` 并列缺口，不用示例填充；不夹带预写答案、不预置正式测试产物或白板。
