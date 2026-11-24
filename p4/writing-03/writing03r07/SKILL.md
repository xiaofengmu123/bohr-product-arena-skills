---
name: academic-format-adaptation
description: 面向学术格式与模板适配的写作 Skill。当需要依据给定科研材料与模板/格式要求，完成学术成稿的模板规格识别、标题层级与图表参考文献排版、格式转换完整性保真、导出结果校验及结构化写作，并为每条事实、数字、引用和格式改动保留“来源定位—逐字摘录—证据强度—修改记录”可回溯证据链，最终产出 /app/output/result.json 时使用。
version: 1.0.0
---

# 学术格式与模板适配 Skill

## 1. 角色与何时使用

- 角色：证据驱动的学术格式适配执行器与质检器。核心目标不是“多写”，而是把真实稿件按目标模板改到“可交付、可复核”，同时让每一处格式改动与每条事实都能被指认“依据在哪、依据有多强”。
- 何时触发：`/input/case.json` 要求依据给定科研材料完成学术成稿的排版适配、模板套用、格式转换、引用/图表格式规范化、导出校验或结构化写作，并要求保留来源、修改记录与不确定性时。
- 何时不用：纯创意写作、无来源闲聊扩写、无模板/格式要求的自由行文、超出给定材料的领域外推测。

## 2. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、任务说明、材料清单、`output_contract`、评分所需证据范围）与 `/input/task.md`（题面与运行时约定）。
- 科研材料与模板：由 `case.json` 指明的论文、稿件、模板或格式规定文件。PDF 用平台 `pdf-text-tool` 抽取全文（必要时 `ocr-tool`）；docx/pptx/xlsx 用 `office-renderer`；LaTeX 源直接读文本。实际文件内容才是事实依据，不按文件名或领域标签脑补。
- 字体：题目模板 revision3 要求使用组织方提供的 Liberation Serif；不可用字体只作历史记录，不据此脑补或改写正文。
- 工作目录：`/app/work`（中间产物、抽取文本、预览）；工具抽取的纯文本（pdf-text-tool/office-renderer/ocr 输出）统一存到 `/app/work/extracted/`，供 `main.py --verify` 做摘录定位；最终实际结果与可编辑产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。

## 3. 输出契约

必产 `/app/output/result.json`，字段 = `case_id` + `case.json` 的 `output_contract` 全部字段（原样回填，缺值给 `null`/`[]`，不改名、不删未知键）+ 以下保留字段：

- `status`：`partial | complete | failed`，诚实标注；材料不足用 `partial` 并写 `uncertainty.limitations`。
- `sources`：来源清单（`id/type/title/path/doi/accessed/locator/verification`）。
- `uncertainty`：`claims_requiring_confirmation`、`conflicting_evidence`、`material_gaps`、`limitations`。
- `pending_human`：需要人工确认的事项清单。
- `change_log`：`[{ts, action, detail}]`，每次落盘追加一条。
- `degradations` / `errors`：降级与异常记录（不崩溃、不编造）。

若 `case.json` 无 `output_contract`，用三段式兜底：`deliverable`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。

本 Skill 针对题目四问，额外落盘核对结构（可为空数组，不编造条目），每项带 `requirement/expected/actual/status/source_locator/quote` 或等价字段：

- `template_spec`：模板规格提取（认得准）——栏数、字号、行距、页边距、标题层级、图表题注、参考文献样式、导出格式。
- `format_checklist`：排版合规核对（排得对）——标题层级与编号、图表题注与交叉引用、参考文献条目、页眉页脚逐项核对。
- `content_integrity`：转换完整性（转得全）——正文、图表、公式、脚注、批注、修订、域代码、目录等单元逐一登记 `present_before/present_after/changed`。
- `export_verification`：导出校验（交得出）——文件可打开、字体嵌入、页面尺寸与页边距、页码与目录、可编辑源文件。

每个案例还需产出可编辑产物（md/tex/docx）及真实渲染预览（pdf/png）到 `/app/output/`，并把路径回填进 `result.json` 的 `deliverable`。

## 4. 执行流程（900 秒 / 64 请求）

硬约束：**前 3 分钟（180 秒）内必须先落盘一版 `result.json` 骨架，之后只做增量改进，不得整体重建。**

- 阶段 0 盘点（0–60s，≤2 请求）：读 `case.json` + `task.md`，解析 `case_id`、`output_contract`、材料清单，列出 `/input` 文件。输入：两个原始文件；输出：任务理解 + 材料清单。
- 阶段 1 骨架落盘（≤180s 前完成，≤1 请求）：运行 `python scripts/main.py --init`，确认 `case_id` 正确、契约字段齐全。输入：`/input/case.json`；输出：`/app/output/result.json`（`status=partial`）。
- 阶段 2 模板规格提取（60–300s，≤20 请求）：从模板文件与格式规定抽取可逐项核对的规格，写入 `template_spec`；模板版本差异与相互冲突的规定并列不隐藏。输入：模板/规定全文；输出：规格清单草案。
- 阶段 3 排版适配与内容保真（180–540s，≤20 请求）：逐项套用模板（标题层级、编号、图表题注、交叉引用、参考文献、页眉页脚），登记 `format_checklist` 与 `content_integrity`；每条事实/数字/引用配 `source_ref` 与逐字 `quote`，写 `fact_source_map`。输入：稿件 + 模板；输出：核对清单 + 事实映射。
- 阶段 4 成稿与可编辑产物（360–780s，≤15 请求）：按体裁/受众/格式写 `deliverable.content`；保存 md/tex/docx 产物 + pdf/png 预览（字体用 Liberation Serif）；校验可打开、字体嵌入、页码目录可用，写入 `export_verification`；回填 `artifact_path`。输入：`fact_source_map` + 核对清单；输出：`deliverable` + 产物文件。
- 阶段 5 自检与收尾（720–900s，≤6 请求）：运行 `python scripts/main.py --verify`；修复硬性错误（缺字段、摘录无法定位、占位符）；写 `status/uncertainty/pending_human/change_log`；预留 ≤2 请求重试。输入：`result.json` + 材料；输出：最终 `result.json` + `verification_report.json`。

每一步都把增量写回 `result.json` 并 `change_log` 追加；任一步超时或请求耗尽时保持 `status=partial`，如实写 `limitations`，绝不编造。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] `case_id` 与 `/input/case.json` 一致（任务完成度/完整性）
- [ ] `result.json` 含 `output_contract` 全部字段 + `status/sources/uncertainty/change_log`（结果完整性）
- [ ] 模板规格逐项有来源定位，未凭经验猜测（认得准/结果正确性）
- [ ] 标题层级、编号、图表题注、交叉引用、参考文献、页眉页脚逐项核对（排得对/结果正确性）
- [ ] 内容单元（正文/图表/公式/脚注/批注/修订/域代码/目录）逐项登记有无删改（转得全/结果完整性）
- [ ] 导出文件可打开、字体嵌入、页码与目录可用、保留可编辑源（交得出/任务完成度）
- [ ] 每条事实/数字/引用有 `claim_id/quote/source_ref/evidence_level/locator`（找得到/对得上）
- [ ] 每条 `quote` 经 `main.py --verify` 校验能在材料全文定位，未定位的已标 `limitations`（结果可核验性）
- [ ] 引用真实存在、元数据准确，排除二手转引/撤稿/版本差异（结果正确性）
- [ ] 证据强度与表述相称：相关≠因果、单篇小样本≠领域共识、冲突证据未隐藏（说得准）
- [ ] 未确认项、材料缺口、异常写入 `version_boundary/uncertainty/pending_human`（版本与边界）
- [ ] 无 TODO/占位符/编造标记，`status` 诚实，缺数据标 `partial/failed`（异常处理/可靠体验）
- [ ] 复现路径完整：核对项编号 → 摘录 → 定位 → 强度理由可独立回查（复现）

## 6. 降级策略（鲁棒性变体）

- 空输入目录 / 缺材料：`main.py` 退出码 0，写合法结果（`status=partial`，`uncertainty.material_gaps` 列出缺口），不抛异常、不编造。
- 坏 JSON / 坏编码：按 `utf-8-sig` + `errors=replace` 读取；解析失败记入 `degradations`，继续处理其余材料；`case.json` 不可解析时 `status=partial` 并标注 `input_error`。
- 缺参数 / 未知参数：`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`；未知参数忽略并记录。
- 超大文件：单文件默认上限 2MB（JSON 8MB），超限跳过并记入 `degradations`，不崩溃。
- 写盘失败：降级到最小合法产物（至少 `case_id/status/sources/uncertainty/change_log`），不让进程崩溃。
- 缺软件（`pdf-text-tool/ocr/latex/office-renderer` 不可用）：标准库脚本仍可读纯文本；标注“预览未生成/依赖不可用”，不伪造渲染文件。
- 原文与摘要/模板相互矛盾：全部列入 `conflicting_evidence`，正文用“存在争议/尚不明确”，不偏向单边。
- 无法校验来源真实性：`sources` 标 `verification="unverified"`，`change_log` 记录核验路径。
- 硬性失败（`result.json` 无效/缺契约字段）：`main.py --verify` 退出码 2，先修复再继续。
- 任何情况禁止编造评测分数、运行结果、文献元数据或科学数据。

## 脚本与资源

- `python scripts/main.py --init`：盘点输入、解析 `case.json`、落盘幂等 `result.json` 骨架（增量合并，不覆盖已写内容）。
- `python scripts/main.py --verify`：契约 + 摘录可核验性自检，产出 `verification_report.json`；空目录/坏 JSON/缺参数/超大文件四类场景验证。
- `python scripts/selftest.py`：以合成边界输入跑上述四类场景 + 正常引用定位场景，自检退出码与产物合法性。
- `references/format-checklist.md`：四问核对口径（认得准/排得对/转得全/交得出）与字段规范。
- `references/evidence-rubric.md`：证据强度分级、逐字摘录与 locator 规范、降级口径。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用。

## 7. 最小调用示例（示意，非正式案例结果）

目录布局：

```text
/input/case.json         # case_id + output_contract + 材料清单
/input/paper.md          # 原稿/材料（纯文本可直接读）
/input/template.docx     # 目标模板（二进制，用 office-renderer 抽取到 /app/work/extracted/）
/app/work/extracted/     # 工具抽取的纯文本，供摘录定位
/app/output/result.json  # 最终结构化结果
```

调用：

```bash
cd /skills/weekly-skill
python scripts/main.py --init       # 阶段 1：先落盘 partial 骨架（幂等增量合并）
python scripts/main.py --verify     # 阶段 5：自检契约完整性与摘录定位
python scripts/selftest.py          # 交付前：边界场景（空输入/坏 JSON/缺参数/超大文件）自检
```

示意 `result.json`（示例，非正式案例结果）：

```json
{
  "case_id": "示例-0000",
  "status": "partial",
  "deliverable": null,
  "fact_source_map": [
    {
      "claim_id": "c1",
      "claim_text": "示例论断",
      "quote": "示例摘录",
      "source_ref": "paper.md",
      "locator": "paper.md#L3",
      "evidence_level": "primary_quote"
    }
  ],
  "version_boundary": null,
  "sources": [{"id": "s1", "type": "text", "title": "示例材料", "path": "/input/paper.md"}],
  "uncertainty": {
    "claims_requiring_confirmation": [],
    "conflicting_evidence": [],
    "material_gaps": [],
    "limitations": []
  },
  "change_log": [{"ts": "2026-01-01T00:00:00Z", "action": "init", "detail": "示意条目"}]
}
```

> 以上内容均为示意，须标注为示例，不得冒充正式案例结果。

