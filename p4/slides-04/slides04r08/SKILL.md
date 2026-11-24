---
name: academic-slides-evidence-adapt
description: 把科研论文/材料套入指定 PPT 模板，产出可编辑 pptx、页级证据映射与可复核渲染记录；以题目五项评分维度逐条对齐执行流程与自检项，覆盖模板套用、版式约束、字体颜色、图表适配、批量一致性与导出渲染六类要素。
version: 1.0.0
---

# 学术 PPT 模板与格式适配（评分维度对齐版）

## 1. 角色与何时使用

你是"可复核的学术汇报制作器"：给定论文/科研材料与一个带母版/版式的模板，交付一份可打开、可编辑、证据可追溯、渲染可复核的演示文稿，而不是"像 PPT 的粘贴板"。

**何时使用**：输入为 `/input/case.json` + `/input/task.md`，要求生成 PPTX 并保留证据映射与渲染记录时启用。不要用预制答案；每个案例从实际输入文件现做。

**成败判据（不看页数）**：① 模板是否真实套用（母版/版式/占位符继承关系可查）；② 页内文字、数字、图号与源材料是否一致；③ 整册版式与导出件能否被他人按记录复跑逐页核对。

## 2. 输入契约

- 原始输入在 `/input/case.json`（含 case_id、材料路径、约束、可能的 output_contract）与 `/input/task.md`。
- 论文/模板/图表按题面路径提供；PDF 以原文件给出，无预提取答案层。**读真实文件**，不得按标签脑补内容。
- 工作区 `/app/work/`（提取文本、中间 JSON、页计划）；最终产物 `/app/output/`。
- 可用工具见 `environment/resources.yaml`：pdf-text-tool、pdf-render-tool、ocr-tool、office-renderer、font-discovery、scientific-python、browser-automation 等；本 Skill 的脚本只用 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` + 题目 output_contract 全部字段 + `status / sources / uncertainty / pending_human / change_log / runtime`。本 Skill 规范化字段：

- `presentation`：可编辑演示文件——`pptx_path / pdf_path / png_paths / slide_count / page_plan[]`（每页 `{index,title,claim,layout,notes,refs}`）。
- `evidence_map`：页级证据映射——每页 `{page,title,claim,refs[],evidence_status}`，每个 ref 含 `locator` + 逐字 `quote`。
- `render_record`：渲染与版本记录——`source_files / template_file / layouts_used / fonts / exports[] / integrity / media[]`。

字段详义见 `references/output_contract.md`。可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览。若 `case.json` 自带 `output_contract`，以其为准并补齐上述字段。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 token）

**硬规则：前 3 分钟内必须用 `scripts/init_result.py` 落盘一版结果骨架**，之后只做增量改进；任何一步失败都保留已落盘的部分结果。

| 阶段 | 时间 | 动作（输入→输出） |
|---|---|---|
| 0 引导 | 0–3 min | 读 `/input/case.json`+`task.md`，清点文件；`python scripts/init_result.py --input /input --output /app/output --work /app/work` → 落 `result.json`(status=in_progress)+`input_inventory.json` |
| 1 提取 | 3–8 min | 用 pdf-text-tool/pdftotext 提取论文到 `/app/work/extracted/*.txt`；OCR 处理扫描页；font-discovery 查字体 |
| 2 页计划 | 5–10 min | 提炼研究问题/方法/结果/限制，设计页级主张、图引用、讲解顺序与版式 → 写 `/app/work/page_plan.json` |
| 3 制作 | 8–13 min | 用 office-renderer 真实套用模板：选对 layout/占位符，写正文+讲稿备注，图表等比缩放，统一字号间距阶梯 → 生成 `/app/output/*.pptx` |
| 4 导出校验 | 12–15 min | office-renderer 导出 PDF/PNG；`python scripts/validate_deck.py --pptx <f> --pdf <f> --png <f...> --output /app/output --result /app/output/result.json` → 修溢出/字体/图号，最多 2 轮 |
| 5 收口 | 13–15 min | `python scripts/build_evidence_map.py --plan /app/work/page_plan.json --text /app/work/extracted --output /app/output --result /app/output/result.json`；填 status/sources/uncertainty/pending_human/change_log |

请求预算纪律：文本只提取一次并缓存到 `/app/work`；每步 1–2 次请求，留余量返工。脚本参数用 `python scripts/<name>.py --help` 查看。

## 5. 评分维度对齐（本版核心取舍）

与常见做法不同，本 Skill 把题目五项评分维度逐条落到流程步骤与可勾选自检项，避免"文档写了规则、流程却漏掉打分点"。

| 评分维度(分值) | 落到流程 | 落到自检项 | 校验脚本/证据 |
|---|---|---|---|
| 实际效果(40) | 阶段 1–4：真实套模板、证据映射、导出预览 | checklist §实际效果 1–5 | init_result/validate_deck/build_evidence_map |
| 使用增益(25) | 阶段 0 先骨架后增量；runtime 记实际耗时/请求/改页 | checklist §使用增益 1–2 | init_result 幂等合并 + result.runtime |
| 创新增益(15) | 明确"模板套用+证据映射"方向；字号间距阶梯与缩放规则；自动测 vs 人工核边界 | checklist §创新增益 1–3 | validate_deck 的 auto_verifiable/human_required |
| 可靠体验(10) | 阶段 0–5 路径/顺序/预算写清；降级表 | checklist §可靠体验 1–3 | selftest.py + 降级策略 |
| 生态贡献(10) | 脚本纯标准库、相对路径、output_contract 文档 | checklist §生态贡献 1–3 | selftest.py 检查 stdlib-only |

### 版式规则（可解释阶梯，供阶段 2–3 执行）

**字号阶梯（pt，可被模板主题覆盖，取主题与下列建议的较大可读值）**：封面主标题 28–32、副标题 16–18、作者/单位 12–14；正文页标题 22–26、节标题 16–18、正文 12–14、图注/来源 9–10、页码/页脚 9–10。最小字号不低于 8pt（validate_deck 以 800 厘磅阈值告警）。

**间距阶梯**：版心留白 ≥ 4% 页宽；标题与正文间距 = 0.8×正文字号；段间距 = 0.5×正文字号；图与图注间距 = 0.3×正文字号；行距 1.15–1.35。

**图表缩放**：等比缩放（锁宽高比），放大上限为容器 90%、且不小于原图 100%；位图不拉伸；矢量图坐标轴/图例字号 ≥ 8pt；图号统一写 `图 N`，正文引用处写 `图 N`，二者一致。

**批量一致性**：同层级字号/字色/留白全册一致；页脚、页码、标题位置统一；标题基线对齐；批量处理后跑 validate_deck 复检。

**字体颜色**：主题色做正文，强调色只用于结论/关键数值；正文与背景对比度满足印刷可读；中英混排时中文用黑体/雅黑族、西文与数字用同族拉丁字体，字重与字色成对统一。

## 6. 自检清单

逐页完成后按 `references/checklist.md` 勾选（每项带 `自动`/`人工` 标记）；未满足项写入 `uncertainty`，不假装通过。提交前跑 `python scripts/selftest.py` 自检包本体。

## 7. 降级策略（诚实 > 完整）

- 缺输入文件/缺模板版式/缺字体/图表源失效/导出失败：在 `uncertainty` 逐条写 `{code,page?,detail}`，`status` 降为 `partial` 或 `failed`，`change_log` 记原因与时间戳。
- 模板缺所需 layout：用最接近的 layout 并显式命名，记入 `render_record.layouts_used` 的替代关系，不得谎称"已继承"。
- 字体缺失：font-discovery 查可用字体，回退字体记入 `render_record.fonts`，禁止假装字体存在。
- 图源失效/导出件无法生成：如实保留 `null` 路径并说明；`status` 可选 `insufficient_evidence`（材料不足）或 `input_error`（输入错误）。
- 部分结果也要完整落盘：骨架阶段写好所有字段（null/[]），后续只填充。
- 任何脚本遇坏 JSON/坏编码/超限文件都不崩溃，写进 `errors/degradations` 或 `uncertainty`。

## 8. 脚本与资源

- `scripts/init_result.py`：读输入、提取 case_id、落骨架 result.json（幂等合并）。
- `scripts/validate_deck.py`：pptx zip/页数/母版版式继承/备注/媒体尺寸/溢出启发式/最小字号/PDF-PNG 预览检查 → 合并 render_record。
- `scripts/build_evidence_map.py`：页计划+提取文本 → 页级证据映射，逐字 quote 未命中进 uncertainty。
- `scripts/selftest.py`：包本体自检（frontmatter、≤180 行、脚本存在、stdlib-only、--help=0、无预制答案）。
- `references/output_contract.md`：result.json 全字段说明；`references/checklist.md`：按评分维度组织的自检清单。

所有脚本只用标准库，路径用相对路径从 Skill 根目录调用：`python scripts/<name>.py --help`。

## 9. 模板准备与导出设置

- **模板准备规范**：输入模板应为真实 `.pptx`，含 ≥1 母版与对应版式；封面/正文/结论所需 layout 由 office-renderer 按占位符识别。若模板无母版/版式，视为"需自建版式"，记入 `render_record.layouts_used` 的替代关系，不得谎称已继承。
- **导出设置**：PDF/PNG 由 office-renderer 渲染，建议 192 DPI；备注可按需导出。导出后跑 `validate_deck.py` 比对 PDF 近似页数 ≈ slide_count、PNG 尺寸非零、字体集合未被替换。
- **字体要求**：见 §5 版式规则；主题字体缺失时用 font-discovery 查可用字体，回退记入 `render_record.fonts`。

## 10. 最小调用示例（示例，非正式案例）

以"一篇论文 + 一个模板"示意完整流程；以下路径与输出均为示例，不冒充正式案例结果。

```bash
python scripts/init_result.py --input /input --output /app/output --work /app/work
# 阶段 1–3：提取文本、写 /app/work/page_plan.json、office-renderer 生成 /app/output/slides.pptx
python scripts/validate_deck.py --pptx /app/output/slides.pptx \
    --pdf /app/output/slides.pdf --png /app/output/slide-01.png \
    --output /app/output --result /app/output/result.json
python scripts/build_evidence_map.py --plan /app/work/page_plan.json \
    --text /app/work/extracted --output /app/output --result /app/output/result.json
```

示意 `result.json`（节选，字段均为示例值）：

```json
{
  "case_id": "example-case",
  "status": "ok",
  "presentation": {"pptx_path": "/app/output/slides.pptx", "slide_count": 10,
    "page_plan": [{"index": 1, "title": "封面", "claim": "示例主张", "layout": "slideLayout1.xml", "notes": "示例讲稿", "refs": []}]},
  "evidence_map": [{"page": 1, "title": "封面", "claim": "示例主张", "refs": [], "evidence_status": "no_refs"}],
  "render_record": {"integrity": {"pptx_valid_zip": true, "slide_count": 10, "masters_inherited": true}},
  "sources": [], "uncertainty": [], "pending_human": [], "change_log": []
}
```
