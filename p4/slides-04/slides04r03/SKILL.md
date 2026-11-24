---
name: academic-slides-selfcheck-deterministic
description: 把科研论文/材料套入指定 PPT 模板并交付可编辑 pptx、页级证据映射与渲染记录；以“批量一致性与确定性自检”为突破方向，先落 partial 骨架再增量改进，用 selftest.py 证明同输入两次产物字节一致。用于学术 PPT 模板与格式适配类任务。
version: 1.0.0
---

# 学术 PPT 模板与格式适配（自检与确定性版）

## 1. 角色与何时使用

你是“学术汇报制作器”：给定论文/科研材料与一个带母版/版式的模板，交付一份**可打开、可继续编辑、证据可追溯、渲染可复核**的学术演示文稿，而不是“像 PPT 的粘贴板”。

**何时使用**：任务输入为 `/input/case.json` + `/input/task.md`，要求生成 PPTX 并保留证据映射与渲染记录时启用。不要用预制答案；每个案例从实际输入文件现做。论文 PDF 以原文件给出、无预提取答案层，**读真实文件，不按标签脑补内容**。

**核心判据**（决定成败，不看页数）：① 指定模板是否被真实套用（母版/版式/占位符继承关系可在 pptx XML 中查到）；② 页内文字、数字、图号与源材料是否一致；③ 整册版式与导出件能否被他人复跑逐页核对。

## 2. 输入契约

- 原始输入在 `/input/case.json`（含 `case_id`、材料路径、约束、可能的 `output_contract`）与 `/input/task.md`。材料/模板/图表按题面路径给出；不假设固定文件名或键名。
- 工作区 `/app/work/`（提取文本、`input_inventory.json`、页计划），最终产物写 `/app/output/`。
- 工具见 `environment/resources.yaml`：pdf-text-tool、pdf-render-tool、ocr-tool、office-renderer、font-discovery、scientific-python 等；本 Skill 的脚本只用 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` + 题目 `output_contract` 全部字段（键名原样回填，未知键给 `null`），再加 `status / sources / uncertainty / change_log`（兼容字段 `errors / degradations / pending_human`）。题目无显式 contract 时使用内置三字段：

- `presentation`：`pptx_path / pdf_path / png_paths / slide_count / page_plan[]`，每页 `{index,title,claim,layout,notes,refs}`。
- `evidence_map`：页级证据映射，每页 `{page,claim,refs[],evidence_status}`；每条 `refs[]` 必须含 `locator`（源文件定位到行/页）与 `quote`（逐字引用，非空）。
- `render_record`：`source_files / template_file / layouts_used / fonts / exports[] / integrity / media`，另含 `overflow_candidates / evidence_issues / issues / generated_at`。

字段详义见 `references/output_contract.md`。可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览；无预览则路径写 `null` 并说明。若 `case.json` 自带 `output_contract`，以其为准并补齐上述字段。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 token）

**硬规则：前 3 分钟内必须落盘一版 partial 结果骨架**，之后只做增量改进；任何一步失败都保留已落盘的部分结果。

| 阶段 | 时间 | 动作（输入→输出） |
|---|---|---|
| 0 引导 | 0–3 min | 读 `/input/case.json`+`task.md`，清点文件；跑 `python scripts/init_result.py --input /input --output /app/output --work /app/work` → 落 `result.json`(status=in_progress)+`input_inventory.json` |
| 1 提取 | 3–8 min | pdf-text-tool/pdftotext 提取为 `/app/work/*.txt`；OCR 处理扫描页；font-discovery 查字体 → 写 `/app/work/extracted/` |
| 2 页计划 | 5–10 min | 提炼研究问题/方法/结果/限制，设计页级主张、图引用、讲解顺序与版式 → 写 `/app/work/page_plan.json`，逐页附 `locator + quote` |
| 3 制作 | 8–13 min | 用 office-renderer 按模板真实套用：选对 layout/占位符，写正文+讲稿备注，图表等比缩放，统一字号间距阶梯 → 生成 `/app/output/*.pptx` |
| 4 导出校验 | 12–15 min | office-renderer 导出 PDF/PNG；跑 `python scripts/validate_deck.py --pptx <f> --pdf <f> --png <f...> --result /app/output/result.json --output /app/output` → 修溢出/字体/图号，最多 2 轮 |
| 5 收口 | 13–15 min | 填充 `presentation.page_plan`、`evidence_map`、`render_record`，填 `status/sources/uncertainty/change_log`，最终写 `result.json` |

**确定性约定**：时间戳经 `--now` 注入（默认当前 UTC，自检传 `fixed`）；JSON 一律 `sort_keys=True` 规范化；`result.json` 不内嵌自身输出路径。同输入两次运行的语义产物一致，`selftest.py` 验证字节级一致。**请求纪律**：文本只提取一次缓存到 `/app/work`，每步 1–2 次请求，留余量返工。

## 5. 自检清单（逐条勾选，对应评分维度；未满足写入 uncertainty，不假装通过）

**实际效果(40)**：
- [ ] 源论文/材料真正写入模板，母版/版式/占位符继承关系可在 pptx XML 查到（`validate_deck.py` 的 masters/layouts/rels）。
- [ ] 页内文字、数字、图号与源材料一致；图表与数据对应，图号与正文引用一致。
- [ ] 覆盖封面→正文→结论主要页类型，六类要素（主线/页结构/图引用/讲解逻辑/模板约束/导出渲染）齐全。
- [ ] 交付可打开、可继续编辑的 pptx，以及真实渲染 PDF/PNG 预览。
- [ ] `evidence_map` 每页 `refs[]` 均有 `locator` + 逐字 `quote`，他人可复跑逐页核对。

**使用增益(25)**：
- [ ] 先落骨架、后增量改进；`change_log` 记录实际 `--now` 时间戳、改动字段，`input_inventory.json` 记录材料清单，供公平对照。
- [ ] 每页备注即讲稿；记录实际耗时/请求数/改页数，不用自述代替证据。

**创新增益(15)**：
- [ ] 突破方向明确：批量一致性与确定性校验（区别于参考包的模板套用侧重）。
- [ ] 字号间距阶梯、图表等比缩放规则可解释，并写入 `references/checklist.md`。
- [ ] `selftest.py` + `validate_deck.py` 证明方法边界：哪些能自动测（zip/页数/版式/字体/PNG 尺寸/证据 locator+quote），哪些只能人工核（排版美观、图号引用一致性、字体真实回退、拉伸变形）。

**可靠体验(10)**：
- [ ] 路径/顺序/时间预算写清；他人能按说明复现“论文+模板→pptx+PDF”。
- [ ] 缺版式、占位符缺失、文本框溢出、字体缺失、图表失效、导出失败均有识别与处置（见 §6）。
- [ ] 重复运行幂等：`init_result.py` 合并而非覆盖；脚本 exit code 语义明确（见 §7）。

**生态贡献(10)**：
- [ ] 模板准备规范、参数含义、导出设置、字体要求、限制与维护信息齐全（`references/output_contract.md`）。
- [ ] 脚本只用标准库、相对路径可执行，可直接复用。
- [ ] 沉淀可分享规则清单（`references/checklist.md`），不夹带预写答案。

## 6. 降级策略（诚实 > 完整）

- **缺输入文件/缺模板版式/缺字体/图表源失效/导出失败**：逐条写入 `uncertainty`（`{code,page?,detail}`），`status` 降为 `partial` 或 `failed`，`change_log` 记原因与时间戳；`errors`/`degradations`/`pending_human` 同步标注。
- 模板缺所需 layout：用最接近 layout 显式命名并记入 `render_record.layouts_used` 的替代关系，不得谎称“已继承”。
- 字体缺失：用 font-discovery 查可用字体，回退字体记入 `render_record.fonts`，禁止假装字体存在。
- 图源失效或导出件无法生成：如实保留 `null` 路径并说明，**不伪造文件、不编造运行分数或软件状态**。
- 部分结果也要完整落盘：骨架阶段就写好所有字段（空值/null/[]），后续只填充。

## 7. 脚本与资源

- `scripts/init_result.py`：读输入、提取 `case_id`、落 partial 骨架 result.json（幂等合并，`--now` 注入时间戳）。exit：0=成功（含输入目录缺失的降级骨架）；2=参数错误。
- `scripts/validate_deck.py`：pptx zip/页数/母版版式继承/备注/媒体尺寸/字体/溢出启发式/PDF-PNG 预览/evidence_map locator+quote → `render_record.json` 并合并回 result.json。exit：0=通过；2=发现硬性问题（仍写记录）或参数错误。
- `scripts/selftest.py`：提交前自检（确定性双跑字节一致、契约骨架、降级、证据契约、frontmatter 与行数）。exit：0=全部通过；1=有失败；2=参数错误。所有合成输入带 `SYNTHETIC_SELFCHECK` 标记，在临时目录生成并清理，不是正式案例。
- `references/output_contract.md`：result.json 全字段说明；`references/checklist.md`：可勾选自检清单与字号/缩放规则。

调用：`python scripts/<name>.py --help` 查看参数；从 Skill 根目录用相对路径调用。
