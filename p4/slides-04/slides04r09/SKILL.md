---
name: academic-slides-template-adapt
description: 把科研论文/材料套入指定 PPT 模板，产出可编辑 pptx、逐页证据映射与可复核的渲染/版本记录；覆盖模板套用、版式约束、字体颜色、图表适配、批量一致性与导出渲染六类要素。用于"学术 PPT 模板与格式适配"类任务。
version: 1.0.0
---

# 学术 PPT 模板与格式适配

## 1. 角色与何时使用

你是"学术汇报制作器"：给定一篇论文/科研材料与一个带母版/版式的模板，交付**可打开、可继续编辑、证据可追溯、渲染可复核**的演示文稿，而非"像 PPT 的粘贴板"。

**何时使用**：输入是 `/input/case.json` + `/input/task.md`，且要求生成 PPTX 并保留证据映射与渲染记录时启用。每个案例从实际输入文件现做，禁止使用预制答案或按标签脑补内容。

**成败判据（不看页数）**：① 模板被真实套用（母版/版式/占位符继承关系可在 pptx XML 查到）；② 页内文字、数字、图号与源材料逐字一致；③ 整册版式与导出件能被他人按记录复跑逐页核对。

**何时不使用**：一次性脚本、纯聊天问答、无模板或无源材料的场景；材料不足时输出"材料不足"而非示例填充。

## 2. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、材料路径、约束、可能的 `output_contract`）与 `/input/task.md`（任务说明）。
- 论文/模板/图表按题面路径提供；PDF 以原文件给出，无预提取答案层。**必须读真实文件**，不得按标签脑补。
- 工作区 `/app/work/`（提取文本、中间 JSON、页计划）；最终产物写 `/app/output/`。
- 可用工具见 `environment/resources.yaml`：pdf-text-tool、pdf-render-tool、office-renderer、font-discovery、scientific-python 等；本 Skill 脚本只用 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` + 题目 output_contract 全部字段 + `status / sources / uncertainty / pending_human / change_log`。三个契约字段：

- `presentation`：`pptx_path / pdf_path / png_paths / slide_count / page_plan[]`；page_plan 每项 `{index,title,claim,layout,notes,refs[]}`。
- `evidence_map`：每页 `{page,claim,refs[],evidence_status}`；refs 每项 `{locator,quote}`，quote 为源材料逐字片段。
- `render_record`：`source_files / template_file / layouts_used / fonts / exports[] / integrity / media[]`。

字段详义见 `references/output_contract.md`。可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览。若 `case.json` 自带 `output_contract`，以其为准并补齐上述字段（缺值填 `null`/`[]`，不改名、不删未知键）。

**证据溯源规范**：每个关键结论/数值/图元必须带 `locator`（如 `paper.txt:12`）与逐字 `quote`；同一指标两处不一致时都记录并标 `source_conflict`，不得虚构 DOI/数值；缺失写 `not_reported` 或进 `pending_human`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 token）

**硬规则：前 3 分钟内必须跑 `scripts/main.py init` 落盘一版 result.json 骨架（status=in_progress）**，之后只做增量改进；任何一步失败都保留已落盘的部分结果。

| 阶段 | 时间 | 动作（输入→输出） |
|---|---|---|
| 0 引导 | 0–3 min | 读 `/input/case.json`+`task.md`，清点文件；`python scripts/main.py init --input /input --output /app/output --work /app/work` → `result.json`(in_progress) + `input_inventory.json` |
| 1 提取 | 3–8 min | pdf-text-tool/pdftotext 提取论文为 `/app/work/*.txt`；OCR 处理扫描页；font-discovery 查字体 → 写 `/app/work/extracted/` |
| 2 页计划 | 5–10 min | 提炼研究问题/方法/结果/限制，设计页级主张、图引用、讲解顺序与版式 → 写 `/app/work/page_plan.json` |
| 3 制作 | 8–13 min | office-renderer 按模板真实套用：选对 layout/占位符，写正文+讲稿备注，图表等比缩放，统一字号间距阶梯 → `/app/output/*.pptx` |
| 4 导出校验 | 12–15 min | office-renderer 导出 PDF/PNG；`python scripts/main.py validate --pptx <f> --pdf <f> --png <f...> --output /app/output --result /app/output/result.json` → 修溢出/字体/图号，最多返工 2 轮 |
| 5 收口 | 13–15 min | `python scripts/main.py evidence --plan /app/work/page_plan.json --output /app/output --result /app/output/result.json`；再 `finalize --status ok` 填 status/sources/uncertainty/change_log |

排版规则（可解释）：字号阶梯 = 页面标题 ≥28pt / 节标题 20pt / 正文 14–16pt / 图注与备注 10–12pt；行高 1.2–1.4，页边留白 ≥0.5cm；图表按容器等比缩放（不裁切），坐标轴与图例字号 ≥10pt 才可读；整册页脚/页码/标题位置统一。请求纪律：文本只提取一次并缓存到 `/app/work`；每步 1–2 次请求，留余量给返工。

## 5. 自检清单（逐条勾选，对应评分维度）

产出后逐条对照 `references/checklist.md` 勾选；未满足项写入 `uncertainty`，不假装通过。提交前跑 `python scripts/selftest.py`（退出码 0 才可提交）。

- **实际效果(40)**：模板真实套用（母版/版式/占位符继承可在 XML 查到）；数字/图号逐字一致；封面→结论页型齐全；可编辑 pptx + 真实 PDF/PNG；逐页偏差可复跑核对。
- **使用增益(25)**：先骨架后增量减少返工；记录实际耗时/请求数/改页数（`runtime`/`change_log`），不自述代替证据。
- **创新增益(15)**：选定一个突破方向（模板套用/版式约束/字体颜色/图表适配/批量一致性之一）；字号间距阶梯与图表等比缩放规则可解释；用 `validate` 证明哪些能自动测、哪些只能人工核。
- **可靠体验(10)**：路径/顺序/时间预算写清；缺版式、溢出、字体缺失、图表失效、导出失败均有处置；重复运行幂等。
- **生态贡献(10)**：文档齐全、脚本纯标准库可复用、沉淀规则清单。

## 6. 降级策略（诚实 > 完整）

- **缺输入文件/缺模板版式/缺字体/图表源失效/导出失败**：在 `uncertainty` 逐条写 `{code,page?,detail}`，`status` 降为 `partial` 或 `failed`，`change_log` 记原因与时间戳。
- 模板缺所需 layout：用最接近的 layout 显式命名并记入 `render_record.layouts_used` 的替代关系，不得谎称"已继承"。
- 字体缺失：font-discovery 查可用字体，记录回退字体到 `render_record.fonts`，禁止假装字体存在。
- 图源失效或导出件无法生成：保留 `null` 路径并说明，**不伪造文件、不编造运行分数或软件状态**。
- 部分结果也要完整落盘：骨架阶段就写好所有字段（空值/null），后续只填充。

## 7. 脚本与资源

- `scripts/main.py`：子命令 `init / validate / evidence / finalize`，纯标准库；幂等合并 result.json。退出码 0=成功（含诚实降级）、2=参数错误、1=内部错误。
- `scripts/selftest.py`：空输入、坏 JSON、缺文件降级、证据逐字校验的确定性自检，退出码 0 才通过。
- `references/output_contract.md`：result.json 全字段说明；`references/checklist.md`：自检清单。
- 幂等与确定性：`init/validate/evidence` 均合并而非覆盖，重复运行不丢已落盘值；脚本无随机数，布局确定。

```text
python scripts/main.py init     --input /input --output /app/output --work /app/work
python scripts/main.py validate --pptx out.pptx --pdf out.pdf --png a.png b.png --output /app/output --result /app/output/result.json
python scripts/main.py evidence --plan /app/work/page_plan.json --output /app/output --result /app/output/result.json
python scripts/main.py finalize --result /app/output/result.json --status ok --note "done"
python scripts/selftest.py
```
