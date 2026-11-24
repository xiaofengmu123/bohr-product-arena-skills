---
name: grant-proposal-writing-skill
description: 基金申请书写作 Skill。读取 /input/case.json 与 task.md 及实际科研材料，产出可直接进入申请书的七要素章节稿与要素对照说明，并交付可复核的 /app/output/result.json（case_id + 题目 output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log）。前 3 分钟先落盘 partial 骨架，全程保留来源、区分"已完成/拟开展"、四闸门自检、不编造事实。适用于立项依据、科学问题、研究目标、研究内容、创新点、技术路线、研究基础与可行性等基金申请书写作任务。
version: 2.0.0
---

# 基金申请书写作可复核交付 Skill

## 1. 角色与何时使用

你是**基金申请书写作执行与自核 Agent**：依据给定科研材料，产出可直接进入申请书的**七要素章节稿**（立项依据、科学问题、研究目标、研究内容、创新点、技术路线、研究基础与可行性）＋**要素对照说明**，并把每一步的事实、来源、修改、边界做成可复核记录。

在以下情况使用本 Skill：
- 运行时输入存在 `/input/case.json` 与 `/input/task.md`，要求交付基金申请书/立项建议书章节稿或改稿；
- 需要"完成产物，也保留来源"——数字、术语、引用、图表、论断必须逐条回溯；
- 需要可复核的 `result.json`，供第三方按同一材料、锚点、原始输出重算同一结论。

不要用它处理与申请书写作无关的任务；不要预写正式测试答案或白板（平台提供，参赛者不交）。

## 2. 输入契约

- 原始输入：`/input/case.json`（案例标识、任务约束、可能的 `output_contract`/`output_schema`/`required_fields`）＋ `/input/task.md`（体裁/受众/格式/字数/引用要求、必需要点、证据范围）。
- 科研材料：位于 `/input` 下（PDF/文本/数据等）。**必须读实际文件内容**，不依据文件名或目录标签猜测；PDF 用平台 pdf-text-tool/ocr-tool 提取，不用预抽取的答案层。
- 工作区：中间文件写 `/app/work`；最终产物与可编辑工件写 `/app/output`（含 `result.json`）。Windows 本地调试时脚本自动回退到 `input/`、`work/`、`output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：
- `case_id`：输入案例标识；
- 题目 `output_contract` 的全部字段：本 Skill 默认契约为 `artifact`（七要素章节稿 + 要素对照说明）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。**若 case.json 显式给出 `output_contract`/`output_schema`/`required_fields`，以题目字段为最高优先级，必须全部原样并入 result.json**（`scripts/core_impl.py::build_skeleton` 已实现回填）；
- 追加字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`quality_gates`（字段含义与取值见 `references/checklist.md`）。

可编辑工件写 `/app/output/`，`artifact.path` 指向章节稿 `.md`；Office/slide/diagram 另附真实渲染的 PDF/PNG 预览。任何未完成部分必须在 `uncertainty`/`version_boundary` 标注，不得伪装完成。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

以 Skill 根目录为 cwd（必要时 `cd /skills/weekly-skill`），脚本用相对路径 `scripts/main.py`。**前三分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进。**

- **Step 0 落盘骨架（0–180s，1–2 次请求）**：读 case.json 头部与 task.md 前半，运行 `python scripts/main.py scaffold`（生成 `/app/output/result.json` 骨架 + `/app/work/source_manifest.json`）。此后所有改动都通过更新该文件完成，禁止推倒重写。
- **Step 1 盘点材料（180–360s，3–6 次请求）**：运行 `python scripts/main.py snapshot` 生成来源快照（大小 + SHA256）；列出材料清单、标题、摘要，确定受众、体裁、格式、字数、引用风格、必需要点；记录材料缺口/冲突。
- **Step 2 事实台账（360–600s，8–15 次请求）**：分段精读材料，逐条提取数字/术语/引用/图表/关键论断，登记到 `/app/work/fact_source_map.json`（数组），每条含 `claim/kind/element/source_ref/locator/quote/tense/status`；`tense` 区分"已完成/拟开展"，冲突与不确定写入 `version_boundary` 与 `uncertainty`。
- **Step 3 起草产物（600–780s，20–30 次请求）**：写 `/app/output/proposal_sections.md`（七要素章节稿）＋ `references/elements.md` 所述的要素对照说明；把"目标/内容/路线/指标"四组列表写入 `/app/work/correspondence.json`；逐段对照台账核对事实与引用；每完成一块就更新 result.json。
- **Step 4 自检（780–870s，5–8 次请求）**：运行 `python scripts/main.py run` 合并台账与四闸门检查；`python scripts/main.py verify` 校验 result.json；运行 `python scripts/main.py selftest`；修复来源缺口与格式问题。
- **Step 5 收尾（870–900s，3–6 次请求）**：最终更新 `artifact.path`、`sources`、`uncertainty`、`pending_human`、`version_boundary`、`change_log`，按实设置 `status=done|partial|failed`；时间不足必须保留 `partial` 并列出 `limitations`。

预算纪律：优先保证 Step 0 与 Step 3 的中间产物已落盘；精读按段落分块，单次响应 ≤16384 token，不把整本 PDF 一次性塞进提示。

## 5. 四闸门（写作判据，详见 `references/elements.md`）

- **立得住**：立项依据写清"别人做到哪一步、还差什么、为什么是我们"，禁止政策口号堆叠。
- **说得准**：创新点写清与具体已有工作的差异，禁用无证据的"首次/国际领先/填补空白"等不可核验修饰词（脚本 `check_unverifiable_superlatives` 逐句扫描）。
- **对得上**：研究目标、研究内容、技术路线与考核指标逐条对应，数量与命名一致，无孤儿条目（脚本 `check_correspondence` 自动比较）。
- **查得到**：每个数字/引用/论断都有 `source_ref`＋逐字 `quote`，区分"已完成/拟开展"。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 任务完成度(15)：产物体裁/受众/格式符合 task.md，是"章节稿＋要素对照说明"而非提纲或模板，`artifact.path` 指向真实文件。
- [ ] 结果正确性(15)：每条事实有 `source_ref` 与 `quote`；`unmapped`/`conflict` 单独说明；已完成/拟开展分清；创新点无不可核验修饰词。
- [ ] 结果完整性(5)：七要素齐全、厚度匹配、要素衔接；目标/内容/路线/指标一一对应。
- [ ] 结果可核验性(5)：`sources` 全、含 SHA256，`change_log` 完整，段落—要素—证据三级对应可回溯。
- [ ] 质量增益(15)：四闸门判据可观察可操作，而非泛泛"准确、完整"。
- [ ] 效率增益(5)：骨架先行 + 台账增量减少返工，`change_log` 体现增量改进。
- [ ] 增益可验证性(5)：结论基于材料证据与来源映射，不靠精选案例或自夸。
- [ ] 新增能力/方法改进(5+5)：把评分要求译成可观察判据；从一致性分歧反推要素缺陷。
- [ ] 创新证据(5)：`uncertainty`/`limitations` 写清边界与失效条件。
- [ ] 调用易用/运行稳定(3+4)：新评分员按文档可独立跑通；脚本仅标准库、路径相对、重复运行一致（`selftest` 全 PASS）。
- [ ] 异常处理(3)：题面歧义、答案缺失、双评分歧、材料超长/乱码、模型拒答均有记录。
- [ ] 文档完整/可复用/分享价值(3+4+3)：SKILL.md + 两个 references 齐全，台账/快照/自检可复用。

## 7. 降级与异常处理

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`；严禁补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ `uncertainty` 写明未能生成的项，保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不得声称已全部核对（脚本单文件 2MB 上限自动跳过并记录）。
- 模型拒答/截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 冲突与分歧 → 记录双方依据，标 `source_conflict`，不擅自选一、不擅自改分。

## 8. 脚本与资源

- `scripts/main.py`：入口，子命令 `run|scaffold|snapshot|verify|selftest`，纯标准库、路径相对、可重复运行。
- `scripts/core_impl.py`：容错读取、来源快照、契约回填、四闸门、result.json 校验。
- `scripts/selftest.py`：交付前自检（SKILL.md 契约、空输入、坏 JSON/编码、确定性、四闸门）。
- `references/elements.md`：七要素规范、四闸门判据、三级对应、已完成/拟开展规则。
- `references/checklist.md`：逐条自检清单、result.json 字段说明、降级策略、最小调用示例。

脚本只用 Python 标准库；如环境无 Python，手工按 references 里的字段执行同样流程。

## 9. 严禁事项

- 不预写正式测试答案/白板；不编造 benchmark 分数、实验数据、DOI、卷期页码、图表内容或运行结果。
- 不把文件名/领域标签当事实；不把未完成的 `in_progress`/`partial` 改成 `done`。
- 不提交隐藏测试或白板；平台提供或生成任何所需对照。
