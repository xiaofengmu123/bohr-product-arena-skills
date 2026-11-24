---
name: academic-format-template-adapter
description: 学术格式与模板适配 Skill。当需要依据科研材料与模板/格式约束，完成标题层级、图表题注与交叉引用、参考文献样式、转换完整性与导出结果的核对、改写或结构化写作，并把产出物及其"事实—来源—修改记录"回填到 /app/output/result.json 时使用。
version: 1.0.0
---

# 学术格式与模板适配 Skill

## 1. 角色与何时使用

- 角色：学术格式适配的执行器与质检器。核心不是"把稿件改得像模板"，而是**认得准、排得对、转得全、交得出**：把模板规格与格式要求抽成可逐项核对的检查项，逐项落地后给出事实来源与改动留痕。
- 何时使用：当 `/input/case.json` 要求基于给定科研材料完成学术写作、改写、格式适配、引用、双语或结构化写作，且需要交付可编辑产物与可复核 `result.json` 时。
- 何时不用：无来源的纯创意扩写；无明确格式/模板约束的闲聊式文本生成；超出材料范围的领域外推测。

## 2. 输入契约

- 原始输入：`/input/case.json`（`case_id`、任务说明、材料清单、`output_contract`）与 `/input/task.md`（题面与运行时约定）。
- 科研材料与模板：以 `case.json` 指明的实际文件为准。文本/CSV 用脚本直接读取；PDF 用平台 `pdf-text-tool` 抽取（必要时 `ocr-tool`、`pdf-render-tool`）；Office 文档用 `office-renderer` 或抽取文本；LaTeX 用 `latex-compiler`。**实际文件内容才是事实依据**，不按文件名或领域标签脑补。
- 工作目录：`/app/work`（中间产物、抽取文本、工作稿）；最终结果与可编辑产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。

## 3. 输出契约（变体：输出契约完整）

必产 `/app/output/result.json`，顶层字段 = `case_id` + `case.json` 的 `output_contract` **全部键（逐一对齐、不改名、不删键）** + 以下保留字段：

| 字段 | 说明 |
|---|---|
| `status` | `answered \| partial \| insufficient_evidence \| input_error`，诚实标注 |
| `deliverable` | 主可编辑产物文件名；`deliverable_path` 同值；`word_count` 字符数 |
| `sources` | 来源清单 `[{source_id, rel_path, path, kind, size, format, ...}]` |
| `uncertainty` | `claims_requiring_confirmation`、`conflicting_evidence`、`material_gaps`、`limitations` |
| `pending_human` | 需人工确认项（如字体缺失、模板版本不明、交叉引用冲突） |
| `change_log` | `[{ts, action, target, detail}]`，每次落盘**追加一条**，绝不整体重建后丢弃历史 |
| `errors` / `degradations` | 异常与降级说明 |
| `runtime` | 版本、步骤、契约键、耗时、`check` 结果、生成时间 |

若 `case.json` 无 `output_contract` 或不可解析，使用三段式兜底契约并写入 `runtime.contract_fallback`：`deliverable`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。无论有无契约，都额外产出可编辑产物（`.md`/`.tex`/`.docx`）并按平台要求提供真实渲染预览（`.pdf`/`.png`），路径回填 `deliverable` 与 `sources`。

## 4. 执行流程（900 秒 / 64 请求）

硬约束：**前 3 分钟（180 秒）内必须先落盘一版 `status=partial` 的合法 `result.json`，之后只做增量改进，不得整体重建。**

- **阶段 0 盘点（0–60s，≤2 请求）**：读 `case.json` + `task.md`，解析 `case_id`、`output_contract`、材料清单。输出：任务理解 + 材料清单。
- **阶段 1 骨架落盘（≤180s，≤1 请求）**：运行 `python scripts/main.py init`（幂等，增量合并），再运行 `python scripts/main.py scaffold` 写入 partial 骨架并追加 `change_log`。输出：`/app/output/result.json`（`status=partial`）。
- **阶段 2 规格抽取与事实锚定（60–300s，≤20 请求）**：运行 `python scripts/main.py survey` 与 `show` 打印带 locator 的原文；抽取模板规格（栏数/字号/行距/页边距/标题层级/图注/参考文献样式/导出格式）与可回溯事实（数字/术语/引用/图表）。输出：`work/draft.json`。
- **阶段 3 格式核对与来源匹配（180–540s，≤20 请求）**：逐项核对标题层级、编号、图表题注与交叉引用、参考文献字段顺序、内容单元完整性；每条论断配 `source_id + locator + quote`；冲突与缺口不隐藏。运行 `python scripts/main.py check`。输出：`work/check_report.json`。
- **阶段 4 成稿与可编辑产物（360–780s，≤15 请求）**：按体裁/受众/格式写 `deliverable`；保存可编辑产物 + 渲染预览；回填路径。运行 `python scripts/main.py build`。输出：主交付 + `result.json` + `format_report.md` + 契约逐键校验报告。
- **阶段 5 自检与收尾（720–900s，≤6 请求）**：运行 `python scripts/selftest.py` 与 `python scripts/main.py selfcheck`；修复硬性错误（缺字段、quote 无法定位、占位符）；写 `status/uncertainty/change_log`；预留 ≤2 请求重试。输出：最终 `result.json` + `verification_report.json`。

任一步超时或请求耗尽时保持 `status=partial`，如实写 `limitations`，绝不编造。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] `case_id` 与 `/input/case.json` 一致（任务完成度）
- [ ] `result.json` 含 `output_contract` 全部字段 + `status/sources/uncertainty/change_log`（结果完整性）
- [ ] 每条实质论断/数字/引用都有 `source_id + locator + quote`（找得到/对得上）
- [ ] 每条 `quote` 经 `check` 校验能在材料全文定位，未定位的写入 `limitations`（结果可核验性）
- [ ] 模板规格逐项来自模板/约束原文，未给出者标 `pending_human`（认得准）
- [ ] 标题层级/编号/图注/交叉引用/参考文献字段顺序逐项核对（排得对）
- [ ] 正文/图表/公式/脚注/批注/域代码/目录的内容单元未丢失、未改写（转得全）
- [ ] 导出文件可打开、字体嵌入、页码与目录可用、可编辑源文件保留（交得出）
- [ ] 冲突信息并列不隐藏；相关≠因果、单篇小样本≠共识（说得准）
- [ ] 未确认项、材料缺口、异常写入 `version_boundary/uncertainty`（版本与边界）
- [ ] 无 TODO/占位符/编造标记，`status` 诚实，缺数据标 `partial/failed`（异常处理）
- [ ] 复现路径完整：论断编号 → 摘录 → 定位 → 改动理由可独立回查（可复现）

## 6. 降级策略

- 缺材料 / 只有摘要 / 全文不可得：记入 `uncertainty.limitations`，只写"摘要表明"，不补写正文细节。
- 缺工具（`pdf-text-tool/ocr/latex/office-renderer` 不可用）：标准库脚本仍读纯文本；标"预览未生成/依赖不可用"，不伪造渲染文件。
- 模板版本不明 / 字体缺失：写入 `pending_human` 与 `version_boundary`，不默认套用某种样式。
- 原文与模板约束冲突、交叉引用冲突：全部列入 `conflicting_evidence`，正文用"存在冲突/待确认"。
- 无法校验来源真实性：`sources` 标 `verification="unverified"`，`change_log` 记录核验路径。
- 硬性失败（缺 `case_id`/JSON 无效/字段缺失）：脚本仍退出码 0 并写合法 `result.json`，`errors` 记录原因；不崩溃、不零产物。
- 任何情况禁止编造评测分数、运行结果、文献元数据或科学数据。

## 脚本与资源

- `python scripts/main.py init|scaffold|survey|show|check|build|selfcheck`：主入口（标准库）。
- `python scripts/selftest.py`：内置自测（临时 fixture 空跑，不写正式案例产物）。
- `scripts/core_impl.py`：共享实现（材料发现、契约解析、文本规范、引用命中、校验）。
- `references/template-checklist.md`：模板规格检查项清单与 locator 约定。
- `references/rubric.md`：评分维度→落点映射与常见失败模式。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用（`cd /skills/weekly-skill && python scripts/main.py ...`）。
