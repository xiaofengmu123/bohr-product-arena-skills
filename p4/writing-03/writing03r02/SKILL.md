---
name: academic-format-adaptation
description: 学术格式与模板适配写作 Skill。当需依据给定科研原稿与目标模板（docx/tex/pdf）完成格式适配、样式绑定、标题层级、图表题注与交叉引用、参考文献格式、转换完整性与导出核验，并以逐条可回溯的“原文摘录—来源定位—证据强度”证据链产出 /app/output/result.json 时使用。核心方法：先从模板文件反推逐项可核对的格式规格（字号/行距/页边距/标题层级/题注/参考文献样式），再落盘 partial 骨架、之后只做增量改进。
version: 1.0.0
---

# 学术格式与模板适配 Skill

## 1. 角色与何时使用

- 角色：证据驱动的学术排版适配执行器与质检器。目标不是“改几处外观”，而是把原稿按目标模板改成可交付稿，同时保证内容单元不丢失、每条改动可追溯“依据在哪、依据有多强”。
- 何时触发：`/input/case.json` 要求依据给定科研原稿与目标模板完成格式适配、样式绑定、改写、引用/格式修正或结构化写作，并要求保留事实—来源映射时。
- 何时不用：无模板/无原稿的纯创作、无来源要求的闲聊式扩写、超出给定材料的事实补写。

## 2. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、任务说明、材料清单、`output_contract`、评分证据范围）与 `/input/task.md`（题面与运行时约定）。实际文件内容才是事实依据，不按文件名或领域标签脑补。
- 材料形态：原稿（docx/tex/pdf/md）、目标模板（docx/tex/pdf/补充规定）。docx/tex 由 `scripts/core_impl.py` 确定性反推规格；pdf 用平台 `pdf-text-tool` 抽取（必要时 `ocr-tool`、`pdf-render-tool`、`office-renderer`）。
- 工作目录：`/app/work` 放中间产物与抽取文本；最终实际结果与可编辑产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。预算用于编排，不可用于编造。

## 3. 输出契约

必产 `/app/output/result.json`，字段 = `case_id` + `case.json` 的 `output_contract` 全部字段（原样回填键，缺值 `null`/`[]`，不改名不删未知键）+ 保留字段：

- `status`：`partial | complete | failed | insufficient_evidence`，诚实标注。
- `deliverable`：目标可编辑产物（`content` + `artifact_paths` 编辑源与渲染预览 + `format_spec_applied`）。
- `fact_source_map`：`[{claim_id, claim_text, quote, locator, source_ref, evidence_level, revision}]`，每条关键结论/数字/术语/图表/引用逐条回溯。
- `version_boundary`：修改差异、待人工确认项、材料缺口、异常。
- `sources`：来源清单（`id/type/title/path/doi/accessed/locator/verification`）。
- `uncertainty`：`claims_requiring_confirmation/conflicting_evidence/material_gaps/limitations`。
- `pending_human`、`errors`、`degradations`、`runtime`、`change_log`（每次落盘追加一条 `{ts,action,detail}`）。

若 `case.json` 无 `output_contract`，用三段式兜底：`deliverable`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。可编辑产物（md/tex/docx）与真实渲染预览（pdf/png）写 `/app/output/` 并回填路径。

## 4. 执行流程（900s / 64 请求，先落盘再增量）

硬约束：**前 3 分钟（180s）内必须先落盘一版 `result.json` 骨架（`status=partial`），此后所有阶段只做增量合并（`core_impl.merge_result`），不得整体重建。**

| 阶段 | 时间预算 | 请求预算 | 输入 | 输出 |
|---|---|---|---|---|
| P0 盘点 | 0–60s | ≤2 | case.json + task.md + 材料清单 | 任务理解、材料清单、`output_contract` 字段 |
| P1 早落盘 | ≤180s 前 | ≤1 | case.json | `python scripts/main.py --skeleton-only` → partial 骨架 |
| P2 规格反推 | 60–300s | ≤8 | 模板 docx/tex + 原稿 | `scripts/core_impl.py` 反推字号/行距/页边距/标题层级/题注/参考文献 + 内容单元盘点（标题/段落/表/图/公式/脚注/引用/目录） |
| P3 适配成稿 | 180–720s | ≤30 | 规格清单 + 原稿 | 样式绑定、标题层级、图表题注与交叉引用、参考文献、转换；写 `deliverable`、`fact_source_map`、`version_boundary` |
| P4 导出与质检 | 360–780s | ≤15 | 成稿 + 模板 | 导出可编辑产物 + pdf/png 预览；逐项核验保真度 |
| P5 自检收尾 | 720–900s | ≤6 | result.json + 材料 | `python scripts/main.py --verify-only`；修复硬性错误；写 `status/uncertainty`；预留 ≤2 请求重试 |

**P3 适配要点（按序执行）**：① 用模板命名样式绑定标题/正文，不得只改字号/加粗（否则目录错位）；② 章节编号规则逐级一致（1/1.1/1.1.1）；③ 表注在表上方、图注在图下方，字号按模板；④ 交叉引用用域/`\ref`，不硬编码“图1/表2”数字；⑤ 参考文献按模板字段顺序、作者截断、标点与中英混排，不照搬原稿样式；⑥ 转换中逐单元核对公式/脚注/批注/修订/域代码/目录未被删除、合并、改写或图片化；⑦ 页面尺寸与页边距用导出件实测，非仅声明；⑧ 字体缺失回退平台提供的 Liberation Serif，标 `font_missing`。每一步写回 `result.json` 并追加 `change_log`；超时或请求耗尽保持 `status=partial`，如实写 `limitations`。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] `case_id` 与 `/input/case.json` 一致（实际效果·任务完成度）
- [ ] `result.json` 含 `output_contract` 全部字段 + `status/sources/uncertainty/pending_human/change_log`（实际效果·完整性）
- [ ] 每条实质论断含 `claim_id/claim_text/quote/locator/source_ref/evidence_level`（实际效果·可核验性）
- [ ] 每条 `quote` 经 `main.py --verify-only` 校验能在材料全文定位，未定位的已标 `limitations`（实际效果·可核验性）
- [ ] 引用真实存在、元数据准确，排除二手转引/撤稿/版本差异（实际效果·正确性）
- [ ] 格式规格与内容同时正确：字号/行距/页边距/题注位置/参考文献样式符合模板，正文/图表/公式/引用/批注未被错误改动（实际效果·正确性）
- [ ] 模板要求的检查项与原稿内容单元都被覆盖（实际效果·完整性）
- [ ] 证据强度与表述相称：相关≠因果、单篇小样本≠共识、冲突证据不隐藏（使用增益·质量）
- [ ] 数字/术语/图表/引用逐条有来源，未新增材料外事实（事实与来源映射）
- [ ] 未确认项、材料缺口、异常写入 `version_boundary/uncertainty`（版本与边界）
- [ ] 可编辑产物 + pdf/png 预览已产出并回填路径（使用增益·可验证）
- [ ] 无 TODO/占位符/编造标记，`status` 诚实，缺数据标 `partial/failed`（可靠体验·异常处理）
- [ ] 复现路径完整：论断编号 → 摘录 → 定位 → 强度理由可独立回查（生态贡献·可复用）

## 6. 降级策略

- 缺模板/缺原稿/只有摘要：如实记入 `uncertainty.limitations`，只写“摘要/材料表明”，不补写缺失正文或格式细节；`status=insufficient_evidence`。
- 缺软件（`latex-compiler/office-renderer/font-discovery` 不可用）：标准库脚本仍可读 docx/tex；预览标“未生成/依赖不可用”，不伪造渲染文件；字体缺失标 `font_missing`。
- 模板版本不明/相互冲突：全部列入 `conflicting_evidence`，正文用“存在冲突/需人工确认”，不偏单边。
- 无法校验来源真实性：`sources` 标 `verification="unverified"`，`change_log` 记录核验路径。
- 硬性失败（缺 `case_id`/字段/JSON 无效）：`main.py --verify-only` 退出码 2，先修复再继续。
- 空输入目录：退出码 0，写合法 `insufficient_evidence` 结果，不抛异常。
- 任何情况禁止编造评测分数、运行结果、文献元数据或科学数据。

## 7. 最小调用示例（示例，非正式案例）

输入（示意）：`/input/case.json` 含 `case_id="demo"`、`output_contract={"deliverable":"按模板排版","fact_source_map":[],"version_boundary":[]}`；材料为 `manuscript.docx` 与 `template.docx`。

调用（在 `/skills/weekly-skill` 根目录）：

```bash
python -B scripts/main.py --input /input --output /app/output --work /app/work
```

示意输出（标注为示例）：`/app/output/result.json` 的 `case_id="demo"`、`status="partial"`，`deliverable.artifact_paths` 含 `manuscript_adapted.docx` 与 `preview.pdf` 路径；`fact_source_map` 每条带 `quote` 与 `locator`。此例仅说明接口，不冒充正式案例结果。

## 8. 脚本与资源

- `python scripts/main.py [--input /input --output /app/output --work /app/work]`：编排入口；`--skeleton-only` 只落骨架（P1 早落盘），`--verify-only` 只做契约/摘录自检。
- `python scripts/core_impl.py [...]`：模板规格反推 + 内容单元盘点 + 骨架/增量合并（幂等）。
- `python scripts/selftest.py`：交付前自检（frontmatter/脚本可运行/冒烟测试）。
- `references/format-spec-checklist.md`：模板规格反推清单与保真度核验项。
- `references/evidence-rubric.md`：证据强度分级、摘录定位与论断—来源记录规范。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用。
