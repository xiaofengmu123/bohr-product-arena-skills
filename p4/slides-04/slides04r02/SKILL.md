---
name: academic-slides-budget-guard
description: 把科研论文/材料套入指定 PPT 模板，交付可编辑 pptx、逐页证据映射与可复核渲染记录；以"900 秒/64 请求分阶段预算 + 前 3 分钟早落盘"为机器化硬约束，覆盖模板套用、版式约束、字体颜色、图表适配、批量一致性与导出渲染六类要素。
version: 1.0.0
---

# 学术 PPT 模板与格式适配（预算守卫变体）

## 1. 角色与何时使用

你是"学术汇报制作器"。给定论文/科研材料与带母版/版式的模板，交付**可打开、可继续编辑、证据可追溯、渲染可复核**的演示文稿，而不是"像 PPT 的粘贴板"。

**何时使用**：运行时输入为 `/input/case.json` + `/input/task.md`，要求生成 PPTX 并保留证据映射与渲染记录。每个案例从实际输入现做，禁止预制答案。

**核心判据（决定成败，不看页数）**：① 模板被真实套用（母版/版式/占位符继承关系可在 pptx XML 查到）；② 页内文字、数字、图号与源材料逐字一致；③ 整册版式与导出件能被他人复跑逐页核对。

## 2. 本变体的设计取舍：时间预算与早落盘

参考包把预算写进流程表但未机器化执行。本包把**预算与早落盘做成可核对的机器状态**，这是与参考包最明显的差别：

- **早落盘（硬规则）**：前 180 秒内必须用 `scripts/init_result.py` 写出一版**字段齐全**的 `/app/output/result.json` 骨架（所有字段以 null/[] 占位），此后只做增量填充。任何一步失败都保留已落盘的部分结果。
- **阶段预算台账**：`scripts/phase_gate.py` 维护 `/app/work/phase_ledger.json`，记录计划 vs 实际的耗时、请求数、里程碑时间戳；`--check` 对"骨架落盘 ≤180s"与"总耗时 ≤900s"做硬校验，退出码语义明确。
- **增量合并幂等**：`init_result.py` 重复运行只补齐缺失的 null/空字段，不覆盖已填内容；`change_log` 记录每次变更，重复运行结果一致。
- 三脚本只用 Python 标准库；PDF 提取、OCR、PPTX 组装、PDF/PNG 导出走 `environment/resources.yaml` 提供的工具，缺工具时降级并如实标注。

## 3. 输入契约

- 原始输入 `/input/case.json`（含 `case_id`、材料路径、约束、可能的 `output_contract`）与 `/input/task.md`。
- 论文/模板/图表按题面路径提供；PDF 以**原文件**给出，无预提取答案层，**读真实文件**，不得按标签脑补。
- 工作区 `/app/work/`（提取文本、页计划、台账），最终产物 `/app/output/`。
- 若 `case.json` 自带 `output_contract`，其全部键原样回填到 result.json（未知键不删除、不改名）。

## 4. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` + 题面 `output_contract` 全部字段，再加 `status / sources / uncertainty / pending_human / change_log / errors / degradations / runtime`。缺省三键：

- `presentation`：`pptx_path / pdf_path / png_paths / slide_count / page_plan[]`（每页 `{index,title,claim,layout,notes,refs}`）。
- `evidence_map`：每页 `{page,claim,refs[],evidence_status,evidence[]}`，`evidence[]` 每项 `{locator,quote}`。
- `render_record`：`source_files / template_file / layouts_used / fonts / exports[] / integrity / media[]`。

字段详义见 `references/output_contract.md`。可编辑 Office/slide 产物必须同时给真实渲染的 PDF/PNG 预览。

## 5. 执行流程（总预算 900 秒 / 64 请求 / 单响应 ≤16384 token）

硬规则：**前 3 分钟必须落盘骨架**，之后只增量改进；每个阶段结束调用 `phase_gate.py --end` 记实际耗时，请求数用 `--requests N` 累加。

| 阶段 | 时间(硬期限) | 请求预算 | 动作（输入→输出） |
|---|---|---|---|
| 0 引导+早落盘 | 0–180s | ≤4 | 读输入、清点文件；`init_result.py --input /input --output /app/output --work /app/work` → 落骨架 result.json(status=in_progress)+input_inventory.json+phase_ledger.json；`phase_gate.py --begin p0_bootstrap` |
| 1 提取 | 180–400s | ≤12 | pdf-text-tool/pdftotext 提取 → `/app/work/extracted/*.txt`；OCR 处理扫描页；font-discovery 查字体 |
| 2 页计划 | 300–520s | ≤8 | 提炼研究问题/方法/结果/限制 → 页级主张、图引用、讲解顺序、版式 → `/app/work/page_plan.json` |
| 3 制作 | 480–760s | ≤16 | 用 office-renderer 真实套用模板：选对 layout/占位符，写正文+讲稿备注，图表等比缩放，统一字号间距阶梯 → `/app/output/*.pptx` |
| 4 导出校验 | 700–850s | ≤8 | 导出 PDF/PNG；逐页核对溢出/字体/图号，最多返工 2 轮 |
| 5 收口 | 820–900s | ≤6 | 填 evidence_map、status/sources/uncertainty，最终写 result.json；`phase_gate.py --end p5_close --check` |

请求预算：阶段合计 54 次，预留 10 次作返工余量。文本只提取一次并缓存 `/app/work`；每步 1–2 次请求。

## 6. 证据溯源（硬要求）

每个关键结论/数值/图号，都要在 evidence_map 中给出 `{locator, quote}`：`locator` 为 `materials/<file>#L起-L止` 或 `<file>.pdf#page=N` 或 `figures/<名>`；`quote` 为**逐字引用**原文片段。冲突信息同时记录并标 `source_conflict`；不虚构 DOI、数值或引用，缺失写 `not_reported` 或进 `pending_human`。

## 7. 自检清单

逐页完成后按 `references/checklist.md` 勾选（对应评分维度：实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10）；未满足项写入 `uncertainty`，不假装通过。提交前跑 `python scripts/selftest.py` 验证包机制。

## 8. 降级策略（诚实 > 完整）

- **缺输入文件/缺模板版式/缺字体/图表源失效/导出失败**：逐条写 `uncertainty[]`，`status` 降为 `partial | insufficient_evidence | input_error | failed`，`change_log` 记原因与时间戳。
- 模板缺 layout：用最接近的 layout 并显式命名，记入 `render_record.layouts_used` 的替代关系，不得谎称"已继承"。
- 字体缺失：用 font-discovery 查可用字体，回退字体记入 `render_record.fonts`，禁止假装字体存在。
- 图源/导出件失败：保留 `null` 路径并说明，**不伪造文件、不编造运行分数或软件状态**。
- 部分结果也要完整落盘：骨架阶段就写好所有字段，后续只填充。

## 9. 脚本与资源

- `scripts/init_result.py`：读输入、写骨架 result.json（幂等合并）+ 台账。`--help` 查看参数。
- `scripts/phase_gate.py`：`--begin/--end/--requests N/--status/--check`，维护并校验阶段预算。
- `scripts/selftest.py`：空输入/坏 JSON/幂等/预算门禁/纯标准库自检，退出码 0 表示通过。
- `references/output_contract.md`：result.json 全字段说明；`references/checklist.md`：自检清单。
- 路径一律用相对路径从 Skill 根目录调用，如 `python scripts/init_result.py --input /input --output /app/output --work /app/work`。
