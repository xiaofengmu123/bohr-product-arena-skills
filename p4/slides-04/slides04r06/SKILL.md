---
name: academic-slides-evidence-pipeline
description: 面向“学术 PPT 模板与格式适配”的 Skill：以两层结构化中间表示（plan.json 内容模型 / figure_model.json 图形模型）驱动“论文+模板→可编辑 pptx+PDF/PNG+逐页证据映射”，全部产物可复核、可重跑、可降级。用于学术汇报的组织页面与证据映射。
version: 1.0.0
---

# 学术 PPT 模板与格式适配（中间表示与可复核）

## 1. 角色与何时使用

你是“学术汇报制作器”：给定论文/科研材料和一个带母版/版式的模板，交付**可打开、可继续编辑、证据可追溯、渲染可复核**的学术演示文稿，而不是“像 PPT 的粘贴板”。

**何时使用**：任务输入是 `/input/case.json` + `/input/task.md`，要求生成 PPTX 并保留证据映射与渲染记录时启用。不要用预制答案；每个案例从实际输入文件现做。

**核心判据（不看页数）**：① 指定模板被真实套用（母版/版式/占位符继承在 pptx XML 可查）；② 页内文字、数字、图号与源材料一致；③ 整册版式与导出件能被他人按记录复跑逐页核对。

**本版侧重点**：把“内容模型”（说什么、页级主张、讲解顺序）与“图形模型”（图在哪、多大、怎么等比放进容器）拆成两个独立、带 schema 版本、可单独校验的结构化中间产物 `plan.json` 与 `figure_model.json`，再驱动排版与导出。这是与“只把 page_plan 内嵌进 result.json”打法的明确取舍：中间层可独立重跑，证据链路从材料→中间表示→成品逐段可核。

## 2. 输入契约

- 原始输入在 `/input/case.json`（含 `case_id`、材料路径、约束、可能的 `output_contract`）与 `/input/task.md`。
- 论文/模板/图表按题面路径提供；PDF 以**原文件**给出，无预提取答案层。**读真实文件**，不得按标签脑补内容。
- 工作区 `/app/work/`（提取文本缓存、`plan.json`、`figure_model.json`、`input_inventory.json`），最终产物写 `/app/output/`。
- 可用工具见 `environment/resources.yaml`（pdf-text-tool、pdf-render-tool、office-renderer、font-discovery、scientific-python 等）；本 Skill 自带脚本只用 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` + 题目 `output_contract` 全部字段 + `status / sources / uncertainty / pending_human / change_log`。核心字段：

- `presentation`：可编辑演示文件 —— `pptx_path / pdf_path / png_paths / slide_count / page_plan[]`。
- `evidence_map`：页级证据映射 —— 每页 `{page, claim, refs[], evidence_status}`，引用定位到源文件行号并带逐字 quote。
- `render_record`：渲染与版本记录 —— `source_files / template_file / layouts_used / fonts / exports[] / integrity / media[]`。
- `intermediate`：`plan / figure_model / inventory` 三个中间产物路径（指向 `/app/work/` 真实文件）。

字段详义、两个中间表示的完整 schema 见 `references/output_contract.md`。可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览；缺页型/溢出/字体/图表/导出任一未达标，如实写入 `uncertainty` 或 `pending_human`，不伪造。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应≤16384 token）

**硬规则：前 3 分钟内必须跑 `scripts/main.py init` 落盘一版合法 result.json 骨架（status=in_progress，全字段已占位）**，之后只做增量改进；任何一步失败都保留已落盘的部分结果。

| 阶段 | 时间 | 动作（输入→输出） |
|---|---|---|
| 0 引导 | 0–3 min | 读 `/input/case.json`+`task.md`，清点文件；跑 `python scripts/main.py init --input /input --output /app/output --work /app/work` → 落 `result.json`(in_progress)+`input_inventory.json`+`plan.json`/`figure_model.json` 骨架 |
| 1 提取 | 3–8 min | 用 pdf-text-tool/pdftotext 提取论文到 `/app/work/`（文本只提取一次并缓存）；OCR 处理扫描页；font-discovery 查字体 → 写入 `/app/work/` |
| 2 中间表示 | 5–10 min | 从主线提炼研究问题/方法/结果/限制 → 写 `plan.json`（页级主张+讲解顺序+refs）；跑 `python scripts/main.py figures` 生成 `figure_model.json`（原生尺寸/宽高比/缩放模式） |
| 3 制作 | 8–13 min | 用 office-renderer 按模板真实套用：按 `plan.json` 的 `layout_key` 选 layout/占位符，按 `figure_model.json` 等比缩放图表，写正文+讲稿备注 → 生成 `/app/output/*.pptx` |
| 4 导出校验 | 12–15 min | office-renderer 导出 PDF/PNG；跑 `python scripts/main.py evidence`（生成 evidence_map）+ `python scripts/main.py verify`（校验 zip/魔数/母版继承/尺寸/quote 定位）→ 修溢出/字体/图号，最多 2 轮 |
| 5 收口 | 13–15 min | 跑 `python scripts/main.py finalize` 收敛 status；填 sources/uncertainty/pending_human，最终写 `result.json` |

请求预算纪律：每步 1–2 次请求，留余量返工；脚本路径用相对路径从 Skill 根目录调用（`python scripts/main.py <cmd> --help` 查看参数）。

## 5. 中间表示与可复核（本版核心）

- `plan.json`：**内容/论证模型**。`narrative` 四要素（问题/方法/结果/限制）→ 页面 `{index, role, title, claim, figure_ids, refs, layout_key, notes, status}`。只写“讲什么、按什么顺序、证据指向哪”，不写像素。
- `figure_model.json`：**图形/排版模型**。每图 `{id, source, native_size, aspect_ratio, caption, number, target_container, scale_mode, refs, evidence, status}`。等比缩放规则 `scale=min(cw/nw,ch/nh)`（fit）/ `max(...)`（fill_crop），任何模式不改原生宽高比，未知尺寸一律 `pending`。
- 可复核链路：`材料 → /app/work 提取文本 → plan/figure_model → pptx → PDF/PNG → verify_report.json`，每一段都有 schema 标签与 sha256，`verify` 输出确定性检查报告，明确“哪些能自动测、哪些只能人工核”。

## 6. 自检清单（逐条勾选，对应评分维度）

按 `references/checklist.md` 逐页勾选；未满足项写入 `uncertainty`，不假装通过。`python scripts/selftest.py` 在不依赖 office-renderer/字体的前提下验证核心契约（空输入、坏 JSON、幂等、图片尺寸、证据定位、pptx/pdf/png 检查）。

## 7. 降级策略（诚实 > 完整）

- **缺输入/缺版式/缺字体/图源失效/导出失败**：`uncertainty` 逐条写 `{code, page?, detail}`，`status` 降为 `partial`/`failed`/`insufficient_evidence`，`change_log` 记原因与时间戳。
- 模板缺所需 layout：用最接近 layout 显式命名并记入 `render_record.layouts_used` 的替代关系，不得谎称“已继承”。
- 字体缺失：用 font-discovery 查可用字体，记录回退字体到 `render_record.fonts`，禁止假装字体存在。
- 图源失效/导出件无法生成：如实保留 `null` 路径并说明，**不伪造文件、不编造运行分数或软件状态**。
- 部分结果也要完整落盘：`init` 阶段就写好全部字段（空值/null），后续只填充；`evidence_map` 定位不到的引用进 `uncertainty`，不写假 quote。

## 8. 脚本与资源

- `scripts/main.py`：发现输入、落骨架（幂等合并）、生成/校验 plan 与 figure_model、证据定位（locator+逐字 quote）、产物完整性校验、status 收敛。
- `scripts/selftest.py`：无外部依赖自检（22 项，退出码 0/1）。
- `references/output_contract.md`：result.json 与两个中间表示的全字段说明、locator 语法、等比缩放规则。
- `references/checklist.md`：自检清单（对应五个评分维度，逐条可勾选）。

子命令：`init`（前 3 分钟先跑）、`figures`、`plan`、`evidence`、`verify`、`finalize`。退出码 0=成功、2=校验失败、3=异常；脚本只用标准库，路径相对可执行。

## 9. 最小调用示例（示意，非正式案例结果）

```bash
# 在 Skill 根目录执行；缺省已指向 /input、/app/output、/app/work
python scripts/main.py init                        # 前 3 分钟内：落 result.json 骨架 + 三个中间产物
python scripts/main.py figures                     # 生成 figure_model.json（原生尺寸/宽高比/缩放模式）
# 中间：由模型读取论文写 plan.json（页级主张+refs），由 office-renderer 生成 pptx 与 PDF/PNG
python scripts/main.py plan --plan /app/work/plan.json
python scripts/main.py evidence                    # 由 refs 在提取文本中定位逐字 quote
python scripts/main.py verify                      # 校验 zip/魔数/母版继承/尺寸/quote 定位
python scripts/main.py finalize                    # 收敛 status 并落 change_log
python scripts/selftest.py                         # 无外部依赖自检（22 项）
```

示意输出（**标明为示例，不冒充正式案例**）：`result.json` 骨架落盘后 `status="in_progress"`，`presentation`/`evidence_map`/`render_record` 全字段占位；`finalize` 后若 pptx 与 PDF 均存在则 `status="ok"`，否则 `partial` 并逐条写 `uncertainty`。

