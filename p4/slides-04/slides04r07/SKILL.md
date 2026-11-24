---
name: slides-robust-adapt
description: 以鲁棒性为核心的学术 PPT 模板适配 Skill：识别模板母版/版式/占位符并真实继承，把论文与图表写入可编辑 pptx，产出逐页证据映射与可复核的渲染记录；对空输入、坏 JSON、缺参数、超大文件、写盘失败均优雅降级，先落 partial 骨架再增量改进。适用于"学术 PPT 模板与格式适配"类任务。
version: 1.0.0
---

# 学术 PPT 模板与格式适配（鲁棒性变体）

## 1. 角色与何时使用

本 Skill 是"学术汇报制作器"：给定科研论文/材料与一个带母版/版式的模板，交付**可打开、可继续编辑、证据可追溯、渲染可复核**的 pptx，而不是"像 PPT 的粘贴板"。

**何时使用**：任务输入为 `/input/case.json` + `/input/task.md`，要求生成 PPTX 并保留证据映射与渲染记录时启用。每个案例从实际输入现做，不使用预制答案。

**核心判据**（决定成败，与页数无关）：① 模板被真实套用（母版/版式/占位符继承关系可在 pptx XML 查到）；② 页内文字、数字、图号与源材料一致；③ 整册版式与导出件可被他人按记录复跑逐页核对。

**本变体的突破方向**：鲁棒性。空输入、坏 JSON、缺参数、超大文件、写盘失败都要优雅降级不崩溃；同时先落 partial 骨架、后增量改进，任何中途异常都不留下不完整产物。

## 2. 输入契约

- 原始输入在 `/input/case.json`（含 `case_id`、材料路径、约束、可能的 `output_contract`）与 `/input/task.md`（任务说明）。论文/模板/图表按题面路径提供；PDF 以原文件给出，无预提取答案层。**读真实文件**，不得按标签脑补内容。
- 工作区 `/app/work/`（提取文本、中间 JSON、页计划），最终产物写 `/app/output/`。
- 可用工具见 `environment/resources.yaml`：pdf-text-tool、pdf-render-tool、office-renderer、font-discovery、scientific-python 等。本 Skill 的三个脚本只用 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id`、题目 `output_contract` 全部字段，再加 `status / sources / uncertainty / pending_human / change_log / errors / degradations / runtime`。三个契约字段（缺值一律 `null`/`[]`）：

- `presentation`：可编辑演示文件——`pptx_path / pdf_path / png_paths / slide_count / page_plan[]`，每页 `{index,title,claim,layout,notes,refs}`。
- `evidence_map`：页级证据映射——每页 `{page,claim,refs[],evidence_status}`，`refs` 定位到源文件行号。
- `render_record`：渲染与版本记录——`source_files / template_file / layouts_used / fonts / exports[] / integrity / media[]`。

字段详义见 `references/output_contract.md`。可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览。若 `case.json` 自带 `output_contract` 模式，以其为准并补齐上述字段。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应≤16384 token）

**硬规则：前 3 分钟内必须用 `scripts/main.py` 落盘一版结果骨架，之后只做增量改进；任何一步失败都保留已落盘的部分结果。**

| 阶段 | 时间 | 动作（输入→输出） |
|---|---|---|
| 0 引导 | 0–3 min | 读 `/input/case.json` + `task.md`，清点文件；跑 `python scripts/main.py init --input /input --output /app/output --work /app/work` → 落 `result.json`(status=in_progress/partial) + `input_inventory.json` |
| 1 提取 | 3–8 min | 用 pdf-text-tool/pdftotext 把论文提取为 `/app/work/*.txt`；OCR 工具处理扫描页；font-discovery 查字体 → 写 `/app/work/extracted/` |
| 2 页计划 | 5–10 min | 从主线提炼研究问题/方法/结果/限制，设计页级主张、图引用、讲解顺序与版式 → 写 `/app/work/page_plan.json` |
| 3 制作 | 8–13 min | 用 office-renderer 按模板真实套用：选对 layout/占位符，写正文+讲稿备注，图表等比缩放，统一字号间距阶梯 → 生成 `/app/output/*.pptx` |
| 4 导出校验 | 12–15 min | office-renderer 导出 PDF/PNG；跑 `python scripts/validate_deck.py --pptx <f> --pdf <f> --png <f...> --output /app/output` → 修溢出/字体/图号，最多返工 2 轮 |
| 5 收口 | 13–15 min | 跑 `python scripts/main.py merge --patch /app/work/evidence_patch.json --output /app/output`；`python scripts/main.py finalize --status <ok|partial|failed> --output /app/output` 填 status/sources/uncertainty |

请求预算纪律：文本只提取一次并缓存到 `/app/work`，勿反复重读整篇；每步 1–2 次请求，留余量返工。脚本调用路径用相对路径（`scripts/main.py`），从 Skill 根目录执行；`python scripts/<name>.py --help` 查看参数。

## 5. 鲁棒性设计（本变体侧重点）

`scripts/main.py` 是鲁棒性核心，执行时遵循：

- 输入发现不假设固定文件名/键名：遍历 `/input`，识别 `case.json`、`task*.md` 与各类材料。
- 文本按 `utf-8-sig → utf-8 → latin-1` 容错读取；坏 JSON 记录到 `errors`，`status=input_error`，继续处理其余材料，不崩溃。
- 单文件上限 2MB，超限跳过并写入 `degradations`（`oversized_skipped`）。
- 缺参数：`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。
- 写盘失败：降级到 `/app/work/result.json` 的最小合法产物；仍失败退出码 3。
- 重复运行幂等：`init` 合并而非覆盖，已填字段在重跑后保留；`merge` 深度合并补丁。
- 退出码语义：`0` 正常（即使 status 为 insufficient_evidence/input_error）；`2` 参数错误；`3` 无法落盘。

## 6. 自检清单（逐条勾选，对应评分维度）

每页产出后按 `references/checklist.md` 勾选；未满足项写入 `uncertainty`，不假装通过。交付前跑 `python scripts/selftest.py`，须 6/6 通过。

- **实际效果(40)**：模板真实套用、数字图号一致、封面→结论页型齐全、可编辑 pptx+真实预览、逐页偏差可复跑核对。
- **使用增益(25)**：先骨架后增量减少返工；记录实际耗时/请求数/改页数，供对照。
- **创新增益(15)**：鲁棒性为突破方向；字号间距阶梯与图表缩放规则可解释；用 `validate_deck.py` 证明哪些能自动测、哪些只能人工核。
- **可靠体验(10)**：路径/顺序/时间预算写清；缺版式、溢出、字体缺失、图表失效、导出失败均有处置；重复运行幂等。
- **生态贡献(10)**：文档齐全、脚本标准库可复用、沉淀规则清单。

## 7. 降级策略（诚实 > 完整）

- **缺输入文件/缺模板版式/缺字体/图表源失效/导出失败**：在 `uncertainty` 逐条写 `{code,page?,detail}`，`status` 降为 `partial` 或 `failed`，`change_log` 记原因与时间戳。
- 模板缺所需 layout：用最接近的 layout 显式命名并记入 `render_record.layouts_used` 的替代关系，不得谎称"已继承"。
- 字体缺失：用 font-discovery 查可用字体，记录回退字体到 `render_record.fonts`，禁止假装字体存在。
- 图源失效或导出件无法生成：如实保留 `null` 路径并说明，**不伪造文件、不编造运行分数或软件状态**。
- 部分结果也要完整落盘：骨架阶段就写好所有字段（空值/null），后续只填充。

## 8. 脚本与资源

- `scripts/main.py`：鲁棒发现输入、提取 case_id、落骨架 result.json（幂等合并）、merge/finalize 增量收口。
- `scripts/validate_deck.py`：pptx zip/页数/母版版式占位符继承/备注/媒体尺寸/溢出启发式/PDF-PNG 预览检查 → `render_validation.json`。
- `scripts/selftest.py`：空输入、坏 JSON、缺参数、超大文件、重复幂等、validate_deck 缺文件六类自检。
- `references/output_contract.md`：result.json 全字段说明；`references/checklist.md`：自检清单。
