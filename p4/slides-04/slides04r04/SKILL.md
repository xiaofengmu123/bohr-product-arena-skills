---
name: academic-slides-evidence-deck
description: 把科研论文/材料套入指定 PPT 模板，产出可编辑 pptx、逐页证据映射与可复核渲染记录；以不确定性显式化与"缺数据不编造"为核心，覆盖模板套用、版式约束、字体颜色、图表适配、批量一致性与导出渲染六类要素。
version: 1.0.0
---

# 学术 PPT 模板与格式适配（证据优先版）

## 1. 角色与何时使用

你是"学术汇报制作器"：给定论文/科研材料和一个带母版/版式的模板，交付一份**可打开、可继续编辑、证据可追溯、渲染可复核**的演示文稿，而不是"像 PPT 的粘贴板"。

**何时使用**：任务输入是 `/input/case.json` + `/input/task.md`，且要求生成 PPTX 并保留证据映射与渲染记录时启用。不要用预制答案；每个案例从实际输入现做。

**核心判据**（不看页数）：① 模板是否被真实套用（母版/版式/占位符继承关系可查）；② 页内文字、数字、图号与源材料是否逐字一致；③ 整册版式与导出件能否被他人复跑逐页核对。

## 2. 输入契约

- 原始输入在 `/input/case.json`（含 `case_id`、材料路径、约束、可能出现的 `output_contract`）与 `/input/task.md`。论文/模板/图表按题面路径提供，PDF 以**原文件**给出，无预提取答案层。**读真实文件**，不得按标签脑补内容。
- 材料路径可能写在 case.json 的任意字段：先扫描 `/input` 目录发现文件，再假设；不假设固定文件名或键名。
- 工作区 `/app/work/`（提取文本、中间 JSON、页计划），最终产物写 `/app/output/`。
- 可用工具见运行时 `environment/resources.yaml`（题目声明含 pdf-text-tool、pdf-render-tool、ocr-tool、office-renderer、font-discovery、scientific-python）。本 Skill 的脚本只用 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` + 题目 `output_contract` 全部字段 + `status / sources / uncertainty / pending_human / change_log`。

- `presentation`：可编辑演示文件 —— `pptx_path / pdf_path / png_paths / slide_count / page_plan[]`（每页 `{index,title,claim,layout,notes,refs}`）。
- `evidence_map`：页级证据映射 —— 每页 `{page,claim,refs[],evidence_status}`；每个 `ref` 带 `locator`（源文件:页码/行号）+ 逐字 `quote`，`evidence_status ∈ verified_quote | partial_quote | inferred | not_reported | source_conflict`。
- `render_record`：渲染与版本记录 —— `source_files / template_file / layouts_used / fonts / exports[] / integrity / media[]`。

字段详义见 `references/output_contract.md`。可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览；渲染失败时保留 `null` 路径并在 `uncertainty` 说明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 token）

**硬规则：前 3 分钟内必须用 `scripts/main.py init` 落盘一版 result 骨架**，之后只做增量改进；任何一步失败都保留已落盘的部分结果，不得清空重来。

| 阶段 | 时间 | 动作（输入→输出） |
|---|---|---|
| 0 引导 | 0–3 min | 读 `/input/case.json` + `task.md`，清点文件；跑 `python scripts/main.py init --input /input --output /app/output --work /app/work` → 落 `result.json`(status=in_progress) + `input_inventory.json` |
| 1 提取 | 3–8 min | 用 pdf-text-tool/pdftotext 把论文提取为 `/app/work/extracted/*.txt`；扫描页用 ocr-tool；font-discovery 查字体 → 写 `/app/work/extracted/` |
| 2 页计划 | 5–10 min | 从主线提炼研究问题/方法/结果/限制，设计页级主张、图引用、讲解顺序与版式 → 写 `/app/work/page_plan.json`（每页含 refs 的 locator+quote） |
| 3 制作 | 8–13 min | 用 office-renderer 按模板真实套用：选对 layout/占位符，写正文+讲稿备注，图表等比缩放，统一字号间距阶梯 → 生成 `/app/output/*.pptx` |
| 4 导出校验 | 12–15 min | office-renderer 导出 PDF/PNG；跑 `python scripts/main.py validate --pptx <f> --pdf <f> --png <f...> --output /app/output --result /app/output/result.json` → 修溢出/字体/图号，最多 2 轮 |
| 5 收口 | 13–15 min | 跑 `python scripts/main.py evidence --plan /app/work/page_plan.json --texts /app/work/extracted --output /app/output --result /app/output/result.json`，再跑 `seal`；填 status/sources/uncertainty，最终写 `result.json` |

预算纪律：文本只提取一次并缓存到 `/app/work`；每步 1–2 次请求，留余量给返工；预算内做不完就降级并如实标注，不压缩事实、不编造结果。

## 5. 自检清单（逐条勾选，对应评分维度）

逐页完成后按 `references/checklist.md` 勾选；未满足项写入 `uncertainty`，不假装通过。提交前跑 `python scripts/selftest.py` 验证健壮性。

- **实际效果(40)**：模板真实套用、数字图号逐字一致、封面→结论页型齐全、可编辑 pptx+真实预览、逐页偏差可复跑核对。
- **使用增益(25)**：先骨架后增量减少返工；`runtime` 记录实际耗时/请求数/改页数，供对照。
- **创新增益(15)**：证据优先 + 不确定性显式化；字号间距阶梯与图表缩放规则可解释（见 `references/checklist.md` 的"可解释排版规则"）；`validate` 明确输出"自动可测"与"仅人工核"边界。
- **可靠体验(10)**：路径/顺序/时间预算写清；缺版式、溢出、字体缺失、图表失效、导出失败均有处置；重复运行幂等。
- **生态贡献(10)**：文档齐全、脚本可复用、沉淀规则清单。

## 6. 降级策略（诚实 > 完整）

- **缺输入/缺模板版式/缺字体/图表源失效/导出失败**：在 `uncertainty` 逐条写 `{code,page,detail,confidence,auto_checkable}`，`status` 降为 `partial | insufficient_evidence | input_error | failed`，`change_log` 记原因与时间戳。
- **证据纪律**：每个关键结论必须能指回材料逐字片段（`locator` + `quote`）；定位不到就写 `evidence_status=not_reported` 或进 `pending_human`，**绝不编造数值、DOI、引用或运行分数**。
- 模板缺所需 layout：用最接近的 layout 显式命名并记入 `render_record.layouts_used` 的替代关系，不谎称"已继承"。
- 字体缺失：用 font-discovery 查可用字体，记录回退到 `render_record.fonts`，不假装字体存在。
- 溢出：标准库只能做**启发式**（单页文本量阈值），真渲染判定靠 office-renderer 的 PDF/PNG；两者都写进 `uncertainty.auto_checkable`，不把启发式冒充为渲染结果。
- 部分结果也要完整落盘：骨架阶段就写好所有字段（null/[]），后续只填充。

## 7. 脚本与资源

- `scripts/main.py`：入口，子命令 `init | validate | evidence | seal | selftest`（`python scripts/main.py <cmd> --help` 查看参数）。
- `scripts/core_impl.py`：纯标准库实现（robust 读取、原子写、pptx zip/XML 检查、证据定位、状态推导）。
- `scripts/selftest.py`：空目录/坏 JSON/缺输入/坏 pptx/非 PDF 等健壮性自检。
- `references/output_contract.md`：result.json 全字段说明；`references/checklist.md`：自检清单与不确定性代码表。

路径用相对路径从 Skill 根目录调用；脚本 exit code：0=成功（含诚实降级），2=参数错误，3=可恢复失败（已落盘 partial），4=致命失败。
