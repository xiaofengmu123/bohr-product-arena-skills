---
name: longdoc-academic-writing
description: 学位论文与长文档写作技能。读取科研材料与任务约束，产出可编辑的章节/改写/校对/结构化写作产物，并保留事实与来源映射、跨章节一致性检查、修改记录与可复核的 result.json。当任务要求依据固定科研材料完成学术写作、修改、格式、引用、双语或结构化写作并交付可复核结果时使用。
version: 1.0.0
---

# 学位论文与长文档写作 Skill

## 1. 角色与何时使用

本 Skill 把一个"给定材料 → 学术写作产物 → 可复核结果"的流程固化下来。目标是四件事同时成立：

- **立得住**：章节规划对应提纲、论证链条与字数分配，而不是把材料堆成通顺段落。
- **连得上**：跨章节术语、定义、符号、图表编号、交叉引用与前后结论保持一致，改动一处能定位受影响段落。
- **改得动**：局部修改只动必要处，保留作者原意，作者能看懂改了什么、为什么改。
- **查得清**：每条论断、引用、改动可回溯到原文/文献/版本记录，他人可独立复核。

适用：依据给定科研材料生成或改写长文档章节、校对术语与引用、合并多轮改稿、输出结构化写作结果。不适用：一次性脚本、无材料可依的纯聊天问答。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：任务说明、材料清单、可能的 `output_contract` 字段与评分证据范围。
- `/input/task.md`：题目说明与运行契约（预算、路径、产物要求）。
- 科研材料按题目提供（PDF/Markdown/Office/文本等），PDF 为原始文件、无预提取答案层。

处理原则：

1. 先发现、再假设：扫描 `/input`，不假设固定文件名/键名；`case.json`、`materials/`、`notes.md` 等都要识别。
2. `output_contract`（若有）必须**原样回填全部键**：缺值给 `null`/`[]`，不改名、不删未知键。
3. 文本按 `utf-8-sig` 读取并容错；坏编码、坏 JSON 降级不崩溃，记入 `degradations`/`errors`。
4. 单文件 2MB 上限，超限跳过并记录。中间产物写到 `/app/work`，最终产物写到 `/app/output`。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：从 case.json/task.md 抽取的案例标识。
- 题目 `output_contract` 的**全部字段**（原样回填到顶层）。
- 附加元数据：`status`、`sources`、`uncertainty`、`change_log`，以及 `errors`、`degradations`、`artifacts`、`pending_human`、`runtime`。

`status` 取 `ok | partial | insufficient_evidence | input_error`，必须诚实。主产物为可编辑文件（`.md`/`.docx`/`.pptx`/`.xlsx`），图形给可编辑 SVG + 真实 PDF；Office/幻灯片/图表类产物应同时给真实 PDF/PNG 预览，路径写入 `artifacts`。证据条目格式为 `{claim, source_id, locator, quote}`，`quote` 必须逐字取自材料。字段与状态语义详见 `references/result_schema.md`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**硬约束：前 3 分钟（180 秒）内必须先落盘一版 `result.json` 骨架（status=partial），此后只做增量改进，任何中途异常都不留空产物。**

1. **发现与建档（0–90 秒，1–3 次请求）**：运行 `scripts/discover.py --input /input --output /app/output --work /app/work`，落盘 `/app/work/inventory.json` 与 `/app/output/result.json` 骨架；读 inventory 了解材料与契约。输入：输入目录；输出：清单 + 骨架。
2. **精读任务与材料（90–240 秒，6–12 次请求）**：完整读 case.json、task.md 与材料，抽取事实底稿、受众、文体、格式约束、证据范围；建立事实/术语/引用底稿。输入：清单；输出：事实库与提纲映射（写入 /app/work）。
3. **规划并生成主产物（240–600 秒，10–20 次请求）**：按提纲逐节生成可编辑产物到 `/app/output/`，每节落盘即保存，先写可用初稿再细化。输入：事实库与提纲；输出：主产物初稿。
4. **一致性与引用核对（600–780 秒，6–12 次请求）**：运行 `scripts/check_consistency.py --draft <主产物>`，逐条把论断/数字/术语/引用绑定到 locator + quote，修正编号、术语、交叉引用与改写偏差。输入：初稿；输出：一致性报告 + 证据映射。
5. **收尾与终检（780–900 秒，2–4 次请求）**：运行 `scripts/finalize.py --output /app/output --work /app/work`，写回证据与 change_log，自检后把 status 从 partial 提为 ok 或保留诚实状态。输入：全部中间产物；输出：最终 result.json。

模型请求分配：只读已见过的文件不重复请求；批量读材料；优先保证第 1、3、5 步的请求。若 900 秒耗尽，返回最近一版合法 partial 结果并说明缺口。

## 5. 脚本调用（相对路径，仅标准库）

- `scripts/discover.py --input /input --output /app/output --work /app/work`：输入发现 + 骨架落盘。
- `scripts/check_consistency.py --draft <草稿.md> --work /app/work`：图/表/公式编号、引用对应、缩写与重复定义检查，只报告不修改。
- `scripts/finalize.py --output /app/output --work /app/work [--status ok] [--report <报告.json>] [--evidence <证据.json>]`：合并校验并写终稿。

三者均可 `--help` 查看参数；缺省路径即运行契约路径。可重复运行，同一输入结果一致。

## 6. 自检清单（对应评分维度，交付前逐条勾选）

**实际效果（40 分）**
- [ ] 按提纲写出可用章节，并交付术语/引用/改动说明，而非通用写作建议。
- [ ] 每条论断与文献对得上，数据/术语/符号/编号与材料一致。
- [ ] 研究问题、方法、结果、讨论、局限及图表/参考文献/附录齐全。
- [ ] 章节文字可追溯到大纲条目、原始材料、文献与版本记录。

**使用增益（25 分）**
- [ ] 跨章节一致性与可编辑性经脚本检查并通过。
- [ ] 术语统一、补引用、重排编号不再依赖反复通读全文。
- [ ] 质量/效率改善有可复核记录（检查报告 + change_log）。

**创新增益（15 分）**
- [ ] 形成基础 Agent 难以稳定实现的能力（一致性检查/证据映射/版本记录）。
- [ ] 方法有实质改进且边界明确（长上下文、超长截断的处置）。

**可靠体验（10 分）**
- [ ] 空目录、坏 JSON、坏编码、超长截断、引用找不到原文、编号不连续、格式不支持均有处置。
- [ ] 重复运行产出稳定可用文本；调用说明与实际一致。

**生态贡献（10 分）**
- [ ] 使用说明、依赖、配置、示例、适用范围、篇幅上限、失败模式、维护信息齐全。
- [ ] SKILL.md、脚本、`references/` 模板可被他人直接复用。

## 7. 降级策略（不允许编造）

- 材料缺失/证据不足：`status=insufficient_evidence`，交付缺口清单，不填充示例答案、不编造文献/DOI/数值；缺失写 `not_reported` 或列入 `pending_human`。
- 缺软件/工具（PDF 解析、Office 渲染、LaTeX 等）：如实写进 `degradations` 与 `limitations`，改用已有文本产物，绝不伪造"软件状态或预览已生成"。
- 冲突信息：两侧都记录并标 `source_conflict`，不擅自择一。
- 坏 JSON/坏编码/写盘失败：捕获后继续处理其余材料，降级到最小合法 partial 产物；超时返回最近一版 partial 并说明。
- 运行期发现 `output_contract` 与本文不符：以实际 case.json 为准，字段全部回填，差异记入 `uncertainty`。

## 8. 边界与维护

本 Skill 聚焦"依据给定材料的可复核长文档写作"，不追求通用大而全；一个案例只解决一类写作子场景。交付前应在不同环境按第 5 节脚本各跑一次 `--help` 与空目录/坏 JSON/缺输入三种降级路径。维护时只改脚本与 references，不携带预写答案或正式测试产物。
