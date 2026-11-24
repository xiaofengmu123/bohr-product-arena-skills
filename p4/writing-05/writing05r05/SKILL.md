---
name: evidence-driven-writing
description: 文献证据驱动写作 Skill。把"需要文献支撑"的学术写作落到「论断清单—证据表—成稿」三段交付：从全文（而非摘要）抽取可判定论断单元，为每条论断匹配带 locator 与逐字 quote 的来源，按研究设计/样本量/重复验证/共识程度给出证据强度（A–D）与支撑范围，再据此校正正文动词与限定语（相关≠因果、单篇≠共识）。产出可逐条回溯的 result.json、可编辑成稿与证据复核报告。用于科研写作、改写、引用校对、双语或结构化写作中要求"每句话都能指认依据在哪、依据有多强"的任务；材料缺失或强度不足时如实标注，不编造事实与引用。
version: 1.0.0
---

# 文献证据驱动写作（evidence-driven-writing）

## 1. 角色与何时使用
本 Skill 面向"证据支撑质量"，交付「论断清单—证据表—成稿」三段结果，而不是只给一篇看似完整的成稿。四问验收：**找得到**（每条实质论断关联到具体文献段落）、**对得上**（文献真实、元数据准、原文真支持这句而非更弱/更强的话）、**说得准**（证据强度与不确定性如实表达）、**复得现**（他人可独立回查重跑）。

适用：把事实底稿/科研材料改写成符合指定体裁、受众、格式的稿件；改写或校对已有稿件并核对引用；为事实、数字、术语、引用、图表保留来源与修改记录。
不适用（如实说明并退出）：纯润色/翻译/排版且明确不要求证据溯源；无任何稿件正文或材料；要求输出整篇"无来源"创作而非证据支撑写作。

## 2. 输入契约
原始输入固定在 `/input/`：
- `/input/case.json`：任务说明、材料路径或内嵌内容、输入约束、`output_contract`（可能在 `output_contract`/`outputContract`/`contract`/`result_schema` 任一键下，也可能缺失）、评分证据范围；
- `/input/task.md`：题目要求；
- 其他材料（PDF/txt/md/图/表）按 case.json 提供；PDF 只有原文，没有预解析答案层。
工作目录 `/app/work/`；最终产物放 `/app/output/`。

铁律：
- 以实际读到的文件内容为准，不按文件名/标签补造内容；材料缺失、冲突、异常分别记录到 `limitations`/`uncertainty`/`errors`。
- 任何数字、术语、引用、图表、关键论断都必须能指回原文 `locator` + 逐字 `quote`；无法回溯的不写入 `deliverable`，确需保留则写入 `uncertainty` 并标 `pending_human`。

## 3. 输出契约
必须产出 `/app/output/result.json`，含：
- `case_id`：取自 case.json（`case_id` > `caseId` > `id`）。
- 题目 `output_contract` 全部字段：优先回显 case.json 中每个键；缺失时按默认三字段补齐——`deliverable`（目标产物）、`sources`（事实与来源映射）、`change_log`（版本与边界）。**契约键一个都不能丢，脚本保证回显。**
- 必须再加：`status`、`claims`（论断清单）、`sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`、`errors`、`runtime`。

字段语义（详见 `references/02_contract_and_selftest.md`）：
- `status`：`complete`｜`partial`｜`insufficient_evidence`｜`input_error`｜`failed`；缺数据/缺来源不得写 `complete`。
- `deliverable`：主结果 = 可编辑成稿（写到 `/app/output/manuscript.md`，`deliverable.files` 给出路径）+ 三段交付摘要。
- `claims`：论断清单，每条含 `id`、`claim`、`location_in_manuscript`、`strength`、`support_scope`、`evidence_refs`、`verb_before/after`、`verdict`。
- `sources`：证据表，逐条 `{id,kind,value,locator,quote,source_doc,source_type,strength,support_scope,verdict,change}`；支持"一论断多来源、一来源多论断"。
- `uncertainty` / `pending_human`：判断不确定、证据不足、需人工确认处。
- `change_log`：`[{time,action,detail}]`，记录 scaffold/analyze/claim_evidence/finalize。
- `limitations`：材料缺口、缺软件、截断、异常、未确认项。
- `errors` / `degradations`：解析失败与降级处理。

locator 统一格式：`<source_doc>#<S{section}>.<P{para}>.<s{sentence}>`；PDF 无章节可用 `<source_doc>#p{page}.para{para}.s{sentence}`；最低定位到段或页。`quote` 必须逐字复制原文，省略处用 `…`，不得改写、不得凭记忆补写。
另写可读版 `/app/output/evidence_report.md`（由脚本从 result.json 渲染，不新增事实）。

## 4. 执行流程（900s / 64 次模型请求；每阶段先落盘 partial）
预算：64 请求、900 秒、单次回复 ≤16384 tokens。三案例重复三次；不编造预算外运行结果。

- **P0 落盘骨架（0–3 min，≤3 请求）**。读 `/input/case.json`+`/input/task.md`，运行：
  `python scripts/main.py scaffold --input /input --output /app/output --work /app/work`
  **硬性要求：前 3 分钟内必须先落盘一版 `result.json`（status=in_progress），此后只做增量改进，绝不因重跑丢失已有结果。** scaffold 默认不覆盖非 `in_progress` 版本。
- **P1 取料（3–8 min，≤12 请求）**。读全部材料：PDF 用 `pdftotext -layout <pdf> /app/work/<name>.txt`，md/txt 直接读；整篇通读，不用摘要替代。超 2MB 文件跳过并记 `limitations`。区分原始研究/综述/指南/预印本。
- **P2 论断—证据（8–13 min，≤40 请求）**。按 `references/01_evidence_strength.md` 拆论断、匹配来源、判强度与支撑范围、定动词与限定语。先写 `/app/work/analysis.json`（论断+证据草表），逐步完善。
- **P3 成稿与收口（13–15 min，≤9 请求）**。写 `/app/output/manuscript.md`（可编辑成稿），把论断/证据/强度/不确定性写入 `/app/work/result_patch.json`，运行：
  `python scripts/main.py finalize --input /input --output /app/output --work /app/work --merge /app/work/result_patch.json`
  finalize 合并补丁→校验契约键→渲染 `evidence_report.md`→落盘终版。
- 预留 ~1 请求 / 60s 处理重试与异常；任一步失败先落盘 `partial` 并标注 `errors`/`limitations`。

脚本从 Skill 根目录以相对路径执行（`scripts/xxx.py`）；若 cwd 不是 Skill 根，用 `/skills/weekly-skill/scripts/xxx.py`。

## 5. 自检清单（对应评分维度）
实际效果
- [ ] 三段交付齐全：`claims`（论断清单）+ `sources`（证据表）+ `deliverable`（成稿）均非空。
- [ ] 每条实质论断都有关联来源；每条来源有 `locator`+逐字 `quote`+`source_doc`。
- [ ] 引用真实存在、元数据与原文一致；无二手转引冒充一手、无凭空引用。
- [ ] 每条论断有 `strength` 与 `support_scope`；动词/限定语与强度相称。
- [ ] 每个数字/术语/图表/关键论断可独立复核（`id`+`locator`+`quote` 齐全）。
使用增益
- [ ] 同材料同要求下比"通用建议"更可执行：每个缺陷给出具体改句/改动词/补来源动作。
- [ ] 证据强度一次给全 + `verdict`（accept/revise/reject/verify），减少返工。
- [ ] 论断、证据、强度、不确定性四项在同源 `result.json` 中可对照，支撑增益验证。
创新增益
- [ ] 含"论断切分—来源匹配—强度分级"至少一项结构级能力（见 `references/01_evidence_strength.md`）。
- [ ] 论断—动词对应表、支撑范围 6 类、来源类型识别可复用。
可靠体验
- [ ] 缺材料/仅摘要/原文与摘要矛盾/证据冲突/格式不支持/截断均识别并写入 `limitations`/`uncertainty`。
- [ ] 空输入、坏 JSON、缺 case.json、重复运行均不崩溃且产合法 result.json（`scripts/selftest.py` 覆盖）。
生态贡献
- [ ] SKILL.md、scripts、references 齐备；依赖（仅标准库）、路径、限制写清；示例标注为示例。

## 6. 降级策略
- 缺稿件文本/缺材料：`status=partial` 或 `insufficient_evidence`，只写已验证部分，`limitations` 写明缺口；不得补造正文。
- 缺软件（无 pdftotext）：用标准库读 txt/md 兜底；PDF 无法解析则记 `limitations`，`status=partial`。
- 材料冲突/歧义：保留多种解读写入 `uncertainty`，`support_scope=conflict`，标 `pending_human`。
- 图表/公式缺失或不可读：不猜内容，记 `limitations`，写"该处有图/公式但无法读取"。
- 数字/引用/术语无法回溯：不写入 `deliverable`；确需保留记 `uncertainty`+`pending_human`。
- 引用只有摘要/预印本/二手转引：标 `source_type` 与 `strength=D`，不得作唯一实质依据。
- 严禁编造评测分数、运行结果、基准对比、隐藏测试或白板；结果只来自本次实际运行。
- 先落盘后分析：任何一步中途异常都已有可读 partial 结果，不产生空白产物。

## 7. 最小调用示例（示例，非正式案例）
假设 case.json 要求"把三段临床材料改写成面向临床医生的 500 字摘要，保留证据来源"：
1. `python scripts/main.py scaffold ...` → 落盘骨架。
2. 读材料，拆出 3 条论断，各配 `locator`+`quote`+`strength`。
3. 写 `manuscript.md` 与 `result_patch.json`（`status=complete`），运行 finalize。
4. 输出 `/app/output/result.json`、`/app/output/manuscript.md`、`/app/output/evidence_report.md`。
（此为接口示例，不冒充正式案例结果。）

## 8. 边界
- 一个 Skill 只解决"证据支撑质量"一类问题；不做通用大而全。
- 无真实材料时输出"材料不足"，不用示例填充。
- 规则详见 `references/01_evidence_strength.md`（强度与切分）、`references/02_contract_and_selftest.md`（契约与自检）。
