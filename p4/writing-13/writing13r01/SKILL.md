---
name: grant-writing-evidence-skill
description: 基金申请书写作的可复核执行 Skill。读取 /input/case.json 与 task.md 及真实科研材料，产出"可直接进入申请书的章节稿＋七要素对照说明"，并用结论级 locator + 逐字 quote 的 evidence_map 保留来源；交付 /app/output/result.json（case_id + 题目 output_contract 全字段 + status/sources/uncertainty/pending_human/change_log）。前 3 分钟先落盘 partial 骨架，之后只做增量改进；缺数据如实标注、不编造。适用于立项依据、科学问题、研究目标、创新点、技术路线、研究基础与可行性、考核指标等基金申请书写作任务。
version: 2.0.0
---

# 基金申请书写作·证据溯源 Skill

## 1. 角色与何时使用

你是**基金申请书写作与自核 Agent**：依据给定科研材料，产出"可直接进入申请书的
章节稿＋七要素对照说明"，而不是写作建议、提纲或整篇模板。本 Skill 的核心变体是
**证据溯源强化**——每条进入正文的关键结论都必须带 `locator`（文件定位）与逐字
`quote`（原文片段），并维护"段落→要素→结论"三级可复核映射。

使用时机：

- 运行时存在 `/input/case.json` 与 `/input/task.md`，要求完成基金申请书写作、
  改稿、引用、双语或结构化产物；
- 需要"完成产物，也保留来源"——数字、术语、引用、图表、政策口径、前期成果论断
  逐条可回溯；
- 需要可复核的 `result.json`，供第三方按同一材料重算同一结论。

不要用它处理非学术写作任务；不要预写正式测试答案或白板（平台提供，参赛者不交）。

## 2. 输入契约

- 原始输入：`/input/case.json`（案例标识、任务约束、可能的 `output_contract` /
  `output_schema` / `required_fields`）+ `/input/task.md`（体裁/受众/格式/字数/
  引用要求、必需要点、证据范围）。
- 科研材料：位于 `/input` 下（PDF/文本/数据/图等）。**必须读实际文件内容**，不
  依据文件名或目录标签猜测；PDF 用平台 pdf-text-tool/ocr-tool 提取，不用预抽取
  答案层。
- 工作区：中间文件写 `/app/work`；最终产物与可编辑工件写 `/app/output`（含
  `result.json`）。本地 Windows 调试时脚本自动回退到 `input/`、`work/`、`output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：输入案例标识；
- 题目 output_contract 的全部字段。本 Skill 默认契约为 `artifact`（目标产物）、
  `fact_source_map`（事实与来源映射，含 claims/trace/conflicts/unmapped）、
  `version_boundary`（版本与边界）。**若 case.json 显式给出 `output_contract` /
  `output_schema` / `required_fields`，以题目字段为最高优先级，全部并入
  result.json**（脚本已把这类字段存为 `contract_hint`）；
- 追加字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、
  `runtime`（字段含义见 `references/checklist.md`）。

可编辑工件（稿件 `.md`/`.tex`/`.docx`）写 `/app/output/`，并在 `artifact.path`
指向；Office/slide/diagram 另附真实渲染的 PDF/PNG 预览。任何未完成部分必须在
`uncertainty`/`version_boundary` 标注，不得伪装完成。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

以 Skill 根目录为 cwd（必要时 `cd /skills/weekly-skill`），脚本用相对路径。
**前 3 分钟内必须先落盘一版合法的 partial 结果骨架，之后只做增量改进、禁止
推倒重写。**

- **Step 0 落盘骨架（0–180 s，1–2 次请求）**：运行 `python scripts/main.py`；
  脚本发现输入、读 case.json、写 `/app/output/result.json` 骨架与
  `/app/work/run_meta.json`，并对 `/input` 做确定性快照（size+SHA256）。此后所有
  改动通过更新该文件完成。若脚本缺失则手写最小 result.json。
- **Step 1 盘点材料（180–360 s，3–6 次请求）**：`python scripts/evidence.py
  snapshot --input-dir /input` 生成来源清单；列出材料清单、标题、摘要，确定
  体裁/受众/格式/字数/引用风格/语言/七要素要求；记录材料缺口与冲突。
- **Step 2 事实台账（360–600 s，8–15 次请求）**：分段精读，逐条登记结论：
  `python scripts/evidence.py add --claim "…" --kind fact --locator
  "materials/x.pdf#p3§2" --quote "原文逐字" --ref materials/x.pdf --status
  mapped`。冲突/无法逐字定位的写 `conflict`/`unmapped` 并记 note，落入
  `version_boundary` 与 `uncertainty`。
- **Step 3 起草产物（600–780 s，20–30 次请求）**：写 `/app/output` 下可编辑
  章节稿＋七要素对照说明（立项依据、科学问题、研究目标、创新点、技术路线、研究
  基础与可行性、考核指标）；逐段对照台账核对事实与引用，用
  `python scripts/evidence.py trace --artifact … --element … --claims C1,C2`
  建立三级映射。每完成一块即更新 result.json。
- **Step 4 自检（780–870 s，5–8 次请求）**：`python scripts/evidence.py verify`
  与 `python scripts/selftest.py --result /app/output/result.json --output
  /app/output`；`python scripts/evidence.py export --output /app/output` 导出
  `evidence_map.json` 并入 result.json；修复来源缺口与格式问题。
- **Step 5 收尾（870–900 s，3–6 次请求）**：最终更新 `artifact.path`、`sources`、
  `uncertainty`、`pending_human`、`version_boundary`、`change_log`，按实设置
  `status=done|partial|failed`；时间不足必须保留 `partial` 并列出 `limitations`。

预算纪律：优先保证 Step 0 与 Step 3 中间产物已落盘；精读按段落分块，单次响应
≤16384 token，不把整本 PDF 一次性塞进提示。

## 5. 脚本与资源

- `scripts/main.py`：落盘骨架 + 来源快照 + 增量合并（Step 0/收尾）。
- `scripts/evidence.py`：证据台账 add/list/verify/export/trace/snapshot。
- `scripts/selftest.py`：result.json 结构自检与空输入鲁棒性 `--demo`。
- `references/rules.md`：不编造红线、证据溯源规范、四问一致判据、冲突处置。
- `references/checklist.md`：逐条自检清单、result.json 字段说明、降级策略。

脚本仅用 Python 标准库；如环境无 Python，手工按 references 字段执行同样流程。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 任务完成度（15）：产物体裁/受众/格式符合 task.md，`artifact.path` 指向真实文件。
- [ ] 结果正确性（15）：每条事实都有 `locator+quote`；`unmapped`/`conflict` 单独说明；"已完成/拟开展"分清。
- [ ] 结果完整性（5）：七要素齐全、厚度匹配，要素间衔接，对照说明给全。
- [ ] 结果可核验性（5）：`sources` 含 SHA256，`change_log` 完整，`trace` 三键闭环。
- [ ] 质量增益（15）：判据可观察可操作，而非泛泛"准确、完整"。
- [ ] 效率增益（5）：台账＋自检减少返工，`change_log` 体现增量改进。
- [ ] 增益可验证性（5）：结论基于材料证据，不靠精选案例或自夸。
- [ ] 新增能力/方法改进（5+5）：把要求译成可观察判据、从分歧反推锚点缺陷。
- [ ] 创新证据（5）：`uncertainty`/`limitations` 写清边界与失效条件。
- [ ] 调用易用/运行稳定（3+4）：新评分员按文档可独立跑通；脚本仅标准库、路径相对、重复运行一致。
- [ ] 异常处理（3）：题面歧义、答案缺失、双评分歧、材料超长/乱码、模型拒答均有记录。
- [ ] 文档完整/可复用/分享价值（3+4+3）：SKILL.md+references 齐全，台账/快照/自检可复用。

## 7. 降级与异常处理

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，
  `status=partial`；严禁补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ 在 `uncertainty` 写明未能生成的项，
  保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不得
  声称已全部核对。
- 模型拒答/截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 冲突与分歧 → 记录双方依据，不擅自选一、不擅自改分。
- 空输入目录 → `status=insufficient_evidence`，退出码 0，写合法 result.json。

## 8. 严禁事项

- 不预写正式测试答案/白板；不编造 benchmark 分数、实验数据、DOI、卷期页码、
  图表内容或运行结果。
- 不把文件名/领域标签当事实；不把未完成的 `in_progress`/`partial` 改成 `done`。
- 不提交隐藏测试或白板；平台提供或生成任何所需对照。
