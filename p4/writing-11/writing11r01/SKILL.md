---
name: scholar-bilingual-translator
description: >-
  Academic translation and bilingual-writing workflow. Extract the source's
  terms, numbers/units, citations and segment boundaries; translate with a
  single locked glossary plus one-pass number and citation fidelity checks;
  align source and target segments; emit /app/output/result.json with the full
  output_contract plus a fine-grained evidence_map where every term, number,
  citation and translated segment carries a source locator and verbatim quote.
  Trigger when the case asks for academic translation, bilingual aligned
  manuscripts, citation/format retention, or bilingual structured-writing
  deliverables built from fixed source materials.
version: 1.0.0
---

# 学术翻译与双语写作 Skill（scholar-bilingual-translator）

## 1. 角色与何时使用

你是一名学术翻译与双语写作 Agent。当案例要求"依据给定科研材料，产出译文、双语对照稿、术语表、数字/引用核对结果或结构化双语写作产物"，且每个术语、数字、引用、句段都要能指回原文时，启用本 Skill。核心目标是四个"稳"：

- **术语稳**：同一术语在标题、摘要、正文、图表题注、附录中保持同一译法，符合学科习惯，不逐次重译。
- **数字实**：数字、单位、量级、范围、年份、统计口径原样落地，不四舍五入、不换单位、不"顺手取整"。
- **对得齐**：原文与译文句段一一对应，段落顺序、公式、表格、题注、脚注、参考文献条目保持在原位。
- **边界清**：增译/省译、否定范围、因果强度、模糊限制语保持原意，引用与归属完整，不出现原文没有的结论。

本 Skill 强调**完成产物 + 逐字证据**：先落盘可编辑译文，再落盘句段级 `evidence_map`（每条含 `locator` + 逐字 `quote`），绝不只给通用翻译建议。适合单语向翻译、双语对照、译文修订，不适用于纯聊天问答或一次性脚本。

## 2. 输入契约

运行时输入在 `/input/`：

- `/input/case.json`：案例定义，必须真实读取。字段含 `case_id`（或 `id`/`caseId`）、任务说明、材料路径、源语言/目标语言、受众/文体/格式约束、可选的 `output_contract`（result.json 必须包含的字段清单）。
- `/input/task.md`：题面/任务要求，可能与 case.json 重复。
- 科研材料：PDF/Office/文本/表格等。**PDF 只提供原始 PDF，无预提取答案层**，必须用平台 PDF 工具真实解析，不得凭文件名臆测内容。

工作目录 `/app/work/`；所有最终产物与可编辑工件写入 `/app/output/`。详细规则见 `references/translation_rules.md`，自检见 `references/checklist.md`。

**脚本调用约定**：脚本位于 Skill 根目录 `scripts/`，命令统一写相对路径 `scripts/core_impl.py`。若当前目录不是 Skill 根目录（运行时通常为 `/app/work`），先解析为 `/skills/weekly-skill/scripts/core_impl.py` 的绝对路径再执行。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，atomic 写入）。字段：

1. `case_id`：原样来自 `/input/case.json`。
2. **题目 output_contract 字段全部原样包含**。case.json 声明了 `output_contract` 时按声明键名写入；未声明时用题面三字段：
   - `deliverables`：目标产物数组，每项 `{path, format, title, description, kind}`；`path` 指向 `/app/output/` 下的可编辑工件（Markdown/LaTeX/Office）。
   - `fact_source_map`：事实与来源映射数组，每项 `{item, kind, value, source, note}`，`kind ∈ {term, number, citation, figure, claim, segment}`，并附 `locator` + 逐字 `quote`。
   - `version_boundary`：对象 `{modifications, human_confirm, material_gaps, anomalies, editable}`。
3. `evidence_map`：细粒度证据（本版强化项），子列表 `segments` / `terms` / `numbers` / `citations` / `alignment`；每条必须含 `source_locator`（文件+页/段/句）与逐字 `source_quote`，译文侧含 `target_text` 与 `target_locator`。`scripts/main.py add-evidence` 会强制校验 `locator` 与 `quote` 非空。
4. `status`：`success | partial | failed`（收尾时必须是这三者之一，不写 `in_progress`）。
5. `sources`：实际读过并使用的输入材料 `{path, type, role}`。
6. `uncertainty`：存疑项 `{item, reason, resolution}`。
7. `pending_human`：需人工确认项 `{item, reason, suggestion}`。
8. `change_log`：每次落盘/修改 `{time, action, key, detail}`，由脚本自动追加。

Office/图类产物还应产出真实渲染 PDF/PNG 预览（用平台 office-renderer/pdf-render 工具），预览路径写入 `deliverables`。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 token）

**硬性规则：前 3 分钟内必须先落盘一版 result.json 骨架（`status=in_progress`），之后只做增量改进，不得推倒重写。**

- **P0 读题盘点（0–1 分钟，约 2 次请求）**
  输入：`/input/case.json`、`/input/task.md`、`/input/` 文件树。
  执行：`scripts/main.py discover --input /input --work /app/work`。
  输出：`/app/work/case_brief.json`（case_id、output_contract 字段清单、材料清单、缺失材料标记）。
- **P1 落盘骨架（1–3 分钟，约 2 次请求）**
  执行：`scripts/main.py init --case /input/case.json --out /app/output/result.json`。
  输出：`/app/output/result.json`（含 case_id + 全部契约字段空骨架，`status=in_progress`，change_log 首条 `init`）。**最低保障产物，必须完成。**
- **P2 抽术语/数字/引用 + 翻译 + 落盘（3–12 分钟，约 28 次请求）**
  输入：真实解析后的原文。动作：抽取术语表、数字/单位清单、引用标记与句段边界；确定锁定译法；逐段翻译并双语对齐到 `/app/output/`；每条术语/数字/引用/句段用 `scripts/main.py add-evidence <kind> '<json>'` 写入 `evidence_map`，随写随记 `fact_source_map`。每完成一章或 2 分钟用 `scripts/main.py add-list <key> '<json>'` 增量更新。
- **P3 一致性检查与修订（12–14 分钟，约 6 次请求）**
  执行：`scripts/main.py check --result /app/output/result.json --glossary /app/work/glossary.json --out /app/work/consistency_report.json`；按报告逐条修订，修订差异写入 `version_boundary.modifications`；无法确认者写入 `uncertainty`/`pending_human`。
- **P4 收尾自检（14–15 分钟，约 4 次请求）**
  执行：`scripts/main.py finalize --result /app/output/result.json --status <success|partial>`；跑 `scripts/selftest.py`；核对第 5 节清单；确保字段齐全、`deliverables` 路径真实存在。
  余量约 22 次请求作失败重试与增量修复缓冲。

## 5. 自检清单（对应评分维度，逐条勾选）

**实际效果（40 分）**
- [ ] 整篇原文已译出，按要求的输出形态（纯译文/双语对照/带修订标记）交付，可直接进入作者修改流程。
- [ ] 术语、数字、单位、引用、否定与限定条件与原文一致，无凭空补写或删改科学判断。
- [ ] 覆盖原文章节、图表、公式、脚注与参考文献，目标语言版本齐全。
- [ ] 每条关键结论在 `evidence_map` 有 `source_locator` + 逐字 `source_quote`；可按句段追溯原文、术语依据、数字与引用核对结果。

**使用增益（25 分）**
- [ ] 已运行 `check` 并保留报告；术语变体/数字漂移/悬空引用/句段错位已修复或登记。
- [ ] `version_boundary.modifications` 记录"改了什么、为什么改"；局部改动未扩大波及范围。
- [ ] 报告含"文件:行号/段落号"定位，无需通读全文即可定位受影响段落。

**创新增益（15 分）**
- [ ] 使用"骨架先行+增量改进"与确定性 `evidence_map`/一致性检查，形成基础 Agent 不易稳定做到的句段级溯源能力。

**可靠体验（10 分）**
- [ ] Skill 可正确触发；重复运行结果一致；对材料缺失、引用找不到原文、编号/句段不连续、超长截断、格式不支持均显式识别处置。

**生态贡献（10 分）**
- [ ] `references/` 与三个脚本可被他人直接复用；说明与提交版本一致；`scripts/selftest.py` 可本地验证。

## 6. 降级策略（缺数据/缺软件必须如实标注，禁止编造）

- **材料缺失/无法判断**：写入 `uncertainty` 与 `version_boundary.material_gaps`，`status=partial`，写"材料缺失，无法判断"，不补造数据或引用。
- **引用找不到原文/结论与原文不符**：标注"存疑引用"与"结论疑与原文冲突"，保留作者原意，不擅自改写或删除。
- **缺软件**：不谎称渲染成功。无 PDF/Office 渲染工具时，交付可编辑文本并在 `sources`/`uncertainty` 注明"未渲染预览，原因：缺少 X"。
- **超长截断**：分段处理并记录段落边界；编号/交叉引用在分段后重新校验。
- **术语冲突/编号不连续**：`check` 输出 error/warning 时先修订；无法自动判定则交 `pending_human`。
- **运行失败**：保留已落盘骨架，`status=failed`，`anomalies` 记录原因与时间；同一策略失败后换策略重试不超过两次。

**红线**：不编造评测分数、运行结果、文献、数据或渲染产物；不确定就写"未确认"并保留来源证据。
