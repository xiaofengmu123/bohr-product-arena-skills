---
name: slide-contract-complete
description: 把科研论文/材料组织成"证据可追溯、可编辑、渲染可复核"的学术 PPT，并以"输出契约完整"为第一优先：先解析 case.json 的 output_contract，逐字段镜像出完整 result.json 骨架并增量填充 presentation/evidence_map/render_record，逐字段校验、保留逐字证据与追加式 change_log。
version: 1.0.0
---

# 学术 PPT 模板与格式适配（输出契约完整变体）

## 1. 角色与何时使用

你是"学术汇报制作器"：给定论文/科研材料与带母版/版式的模板，交付**可打开、可继续编辑、证据可追溯、渲染可复核**的演示文稿，而非"像 PPT 的粘贴板"。

**何时使用**：输入为 `/input/case.json` + `/input/task.md`，要求生成 PPTX 并保留证据映射与渲染记录时启用。每个案例从实际输入文件现做，禁止预制答案。

**本版突破方向（创新增益）**：输出契约完整。多数 Skill 用三个硬编码字段拼 result.json，字段一改就丢；本 Skill 用 `scripts/contract.py` 把 `case.json` 中**真实的 output_contract** 当作唯一事实来源，逐字段镜像成完整骨架，任何一步都不得丢失已声明字段，并保留追加式 `change_log`。核心判据：① 指定模板被真实套用（母版/版式/占位符继承可查）；② 页内文字、数字、图号与源材料逐字一致；③ result.json 与 output_contract 字段一一对齐且他人可复跑逐页核对。

## 2. 输入契约

- 原始输入在 `/input/case.json`（含 `case_id`、材料路径、约束、可能的 `output_contract`）与 `/input/task.md`。
- 论文/模板/图表按题面路径提供；PDF 为原文件，无预提取答案层。**读真实文件**，不得按标签脑补内容。
- 工作区 `/app/work/`（提取文本、中间 JSON、页计划、契约报告），最终产物写 `/app/output/`。
- 可用工具见 `environment/resources.yaml`（pdf-text-tool、pdf-render-tool、ocr-tool、office-renderer、font-discovery、scientific-python 等）；本 Skill 的脚本只用 Python 标准库。
- 读取约定：`utf-8-sig` 容错；坏 JSON/坏编码降级不崩溃；单文件超 2MB 跳过并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` + 题目 `output_contract` 的**全部字段**（若有则原样回填，不改名、不删未知键；否则用默认契约 presentation / evidence_map / render_record），再加信封字段：

- `status`：`ok / partial / insufficient_evidence / input_error / failed / in_progress` 之一。
- `sources / uncertainty / change_log / pending_human / errors / degradations / runtime`。
- 三个主产物字段：`presentation`（`pptx_path / pdf_path / png_paths / slide_count / page_plan[]`）、`evidence_map`（每页 `{page, claim, refs[], evidence_status}`，refs 含 locator+逐字 quote）、`render_record`（`source_files / template_file / layouts_used / fonts / exports[] / integrity / media[]`）。

字段详义与 item schema 见 `references/output_contract.md`。可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览；若 `case.json` 自带 `output_contract`，以其为准并补齐上述字段。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 token）

**硬规则：前 3 分钟内必须运行 `scripts/main.py init` 落盘一版完整结果骨架**，之后只做增量改进；任何一步失败都保留已落盘的 partial 结果。

| 阶段 | 时间 | 动作（输入→输出） |
|---|---|---|
| 0 引导 | 0–3 min | 读 `/input/case.json`+`task.md`；跑 `python3 scripts/main.py init --input /input --output /app/output --work /app/work` → 落完整 `result.json`(status=in_progress)+`input_inventory.json`+`output_contract.json`。跑 `python3 scripts/main.py contract` 查看要填的字段。 |
| 1 提取 | 3–8 min | pdf-text-tool/pdftotext 提取论文到 `/app/work/*.txt`；OCR 处理扫描页；font-discovery 查字体 → 写 `/app/work/extracted/`。文本只提取一次并缓存。 |
| 2 页计划 | 5–10 min | 提炼研究问题/方法/结果/限制主线，设计页级主张、图引用、讲解顺序与版式 → 写 `/app/work/page_plan.json` 并回填 `presentation.page_plan` 与 `evidence_map`（每条 refs 带 locator+quote）。 |
| 3 制作 | 8–13 min | 用 office-renderer 真实套用模板：选对 layout/占位符，写正文+讲稿备注，图表等比缩放，统一字号间距阶梯 → 生成 `/app/output/*.pptx`，回填 `pptx_path / render_record`。 |
| 4 导出校验 | 12–15 min | office-renderer 导出 PDF/PNG；跑 `python3 scripts/main.py validate --input /input --output /app/output --work /app/work`（exit 2 表示契约违规）→ 修溢出/字体/图号/缺字段，最多 2 轮。 |
| 5 收口 | 13–15 min | 跑 `python3 scripts/main.py finalize --output /app/output --status ok|partial`；把未满足项写入 `uncertainty`/`degradations`，最终写 `result.json` 并再 validate 一次。 |

请求预算纪律：文本提取一次并缓存，勿反复重读整篇；每步 1–2 次请求，留余量返工。脚本参数用 `python3 scripts/<name>.py --help` 查看，从 Skill 根目录以相对路径调用。

## 5. 自检清单

逐页完成后按 `references/checklist.md` 勾选；未满足项写入 `uncertainty`，不假装通过。提交/交付前跑：

```bash
python3 -B scripts/selftest.py            # 六项包级自检，exit 0 才通过
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
```

要点对应评分维度：实际效果(40)——模板真实套用、数字图号一致、页型齐全、可编辑 pptx+真实预览、逐页偏差可复跑；使用增益(25)——先骨架后增量、记录实际耗时/请求数/改页数；创新增益(15)——输出契约完整方向，契约引擎证明"哪些字段能自动校验、哪些只能人工核"；可靠体验(10)——路径/顺序/预算写清、异常有处置、重复运行幂等；生态贡献(10)——文档齐全、脚本可复用、沉淀规则清单。

## 6. 降级策略（诚实 > 完整）

- 缺输入文件/缺模板版式/缺字体/图表源失效/导出失败：逐条写入 `uncertainty`（`{code, page?, detail}`）与 `degradations`，`status` 降为 `partial`/`failed`/`input_error`，`change_log` 记原因与时间戳。
- 模板缺所需 layout：用最接近的 layout 显式命名并记入 `render_record.layouts_used` 的替代关系，不得谎称"已继承"。
- 字体缺失：用 font-discovery 查可用字体，记录回退字体到 `render_record.fonts`，禁止假装字体存在。
- 图源失效或导出件无法生成：如实保留 `null` 路径并说明，**不伪造文件、不编造运行分数或软件状态**。
- 部分结果也要完整落盘：骨架阶段就写好所有字段（空值/null），后续只填充；`init` 幂等合并不覆盖已有工作。

## 7. 脚本与资源

- `scripts/contract.py`：契约引擎（discover/materialize/merge/validate/append_change），被 main.py 复用。
- `scripts/main.py`：编排器，子命令 `init / contract / validate / finalize`，exit 0=ok、2=契约违规、1=用法错误。
- `scripts/selftest.py`：包级自检（骨架镜像/init/validate 前置与通过/幂等/空输入降级）。
- `references/output_contract.md`：result.json 全字段说明；`references/checklist.md`：自检清单。
