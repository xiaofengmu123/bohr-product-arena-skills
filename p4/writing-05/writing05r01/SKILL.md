---
name: evidence-driven-writing
description: 文献证据驱动写作 Skill（证据溯源强化版）。把依据固定科研材料的写作/改写/校对/引用/结构化任务拆成"论断清单—证据表—成稿"三段交付：从全文抽取可直接引用的原文片段并保留章节/段落/句子定位，为每条实质性论断匹配支撑来源、支撑关系、证据类型与证据强度，按强度控制正文表述与不确定性，并给出每条结论的 locator+逐字 quote 证据映射；脚本对 quote 做逐字回查，防止把弱证据写成强结论或编造来源。用于要求每条有依据的话都能指回"依据在哪里、依据有多强"、可被独立复核的学术写作任务。
version: 1.1.0
---

# 文献证据驱动写作（evidence-driven-writing）

## 1. 角色与何时使用

本 Skill 解决"写出来的每一句有依据的话都能被指认依据在哪里、依据有多强"：产出可编辑成稿，同时保留可逐条回溯的论断—来源映射。核心不是让模型多写，而是把任务落到**论断清单—证据表—成稿**三段交付，并让证据可独立回查。

适用：依据固定科研材料完成学术写作、改写、校对、引用/格式、双语或结构化写作；题目要求"找得到、对得上、说得准、复得现"。
不适用（如实说明并退出）：无任何材料正文、只要求纯语言润色且不要求证据溯源、要求整篇重写且不接受论断切分。

## 2. 输入契约

运行时原始输入固定：
- `/input/case.json`：任务说明、材料路径或内嵌内容、输入约束、`output_contract` 字段、评分证据范围；
- `/input/task.md`：题目要求；
- 其他材料（PDF/txt/md/图/表/数据）按 case.json 提供；PDF 只有原文，无预解析答案层。

铁律：以实际读到的文件内容为准，不按标签或文件名补造内容；材料缺失、冲突、异常分别记录。工作目录 `/app/work/`，最终产物放 `/app/output/`。Skill 装于 `/skills/weekly-skill`；脚本从 Skill 根目录以相对路径调用 `scripts/xxx.py`。

## 3. 输出契约

必须产出 `/app/output/result.json`：
- `case_id`：取自 case.json（`case_id` > `caseId` > `id`；再取 task.md 的 `challenge_id`）。
- 题目 `output_contract` 全部字段：回显 case.json 中 `output_contract`/`outputContract`/`contract`/`result_schema` 的每个键（缺值给 `null`，不改名不删键）；缺失时按默认契约 `deliverable`、`sources`、`change_log` 补齐。
- 必须再加：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`。

字段语义：
- `status`：`complete`|`partial`|`failed`|`input_error`|`insufficient_evidence`；缺数据不得写 `complete`。
- `deliverable`：主结果 = `draft`（成稿）+ `claim_list`（论断清单）+ `evidence_map`（证据表）+ `summary`。
- `evidence_map`：逐条 `{claim_id, claim, claim_kind, claim_scope, final_wording, support[], uncertainty, citation_refs, status}`；`support[]` 每条含 `source_doc`、`locator`、`quote`（逐字）、`relation`、`evidence_type`、`strength`、`strength_rationale`、`verdict`。取值口径见 `references/evidence_rubric.md`。
- `sources`：逐条 `{key,value,kind,location,source_doc,change}`；数字/术语/引用/图表/关键论断均可回溯，不新增材料外事实。
- `uncertainty` / `pending_human`：判断不确定、证据不足、需人工确认处。
- `change_log`：`[{time,action,detail}]`，记录 scaffold/extract/analyze/validate/finalize。
- `limitations`：材料缺口、缺软件、截断、异常、未确认项。

locator 统一 `S{section}.P{para}.s{sentence}`；无法分节用 `P{para}` 或字符区间。另写同源可读版 `/app/output/evidence_report.md`（不新增事实）。

## 4. 执行流程（总 900s / 64 次请求；每阶段先写 partial，之后只增量改进）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架（status=in_progress），此后绝不因重跑丢失已改进结果。**

- P0 读题+骨架（0–3 min，≤3 请求）。读 task.md + case.json + 目录清单；运行 `python scripts/main.py scaffold`。输出 `/app/output/result.json`（骨架）+ `/app/work/manifest.json`。
- P1 取料+建索引（3–9 min，≤15 请求）。运行 `python scripts/main.py extract`（PDF 用 `pdftotext -layout`，缺工具则记 limitation）与 `python scripts/main.py analyze`；输出 `/app/work/texts/*.txt`、`/app/work/locator_index.json`、`/app/work/analysis.json`。通读全文，不用摘要替代。
- P2 论断切分+证据匹配（9–13.5 min，≤30 请求）。对每条实质论断填 `new_claim_entry`/`new_support_entry`：抽逐字 quote、定 relation/strength/verdict、写不确定性表述，再写 `deliverable.draft`。一条论断可多来源、一篇文献可支撑多条论断；冲突证据两条都记录。
- P3 组装+校验（13.5–15 min，≤10 请求）。写回 result.json；运行 `python scripts/main.py finalize` 校验契约字段并逐字回查每条 quote，渲染 `/app/output/evidence_report.md`。按校验结果定 `complete`/`partial`。
- 预留约 6 请求 / 60s 处理重试与异常；任一步失败先落盘 partial 并标注，不虚构结果。

## 5. 自检清单（对应评分维度，逐条可勾选）

实际效果
- [ ] 三段交付齐备（claim_list / evidence_map / draft）；每条实质论断都进入证据表，不是只挂表面引用。
- [ ] 每条论断定位到 source_doc + locator，quote 与原文逐字一致；无虚构来源、无遗漏真实断裂。
- [ ] 每条建议是具体编辑动作（改写为…/加限定语/删除/换来源），不是通用写作原则。
- [ ] 相关性不写成因果、单篇小样本不写成共识、冲突证据不隐藏。
- [ ] 每条判断可独立复核（claim_id + locator + quote + source_doc + strength_rationale 齐全）。

使用增益
- [ ] 同材料同要求下比"通用改写"更可回溯，减少回原文核对、补引用、改写过度推断的返工。
- [ ] 三段一次给全 + verdict（accept/hedge/reject/pending_human），减少作者返工轮次。
- [ ] 同稿同版本同口径记录在 sources/change_log，支撑增益对照。

创新增益
- [ ] 含"论断—来源关联 + 证据强度 + 逐字 quote 回查"的结构级产物。
- [ ] `evidence.py`（locator 索引 + 逐字回查）、`evidence_rubric.md`（分级口径）、`selftest.py` 可独立复用。

可靠体验
- [ ] 缺材料/仅摘要/原文与摘要矛盾/证据冲突/格式不支持/截断/无 pdftotext 均识别并写入 limitations。
- [ ] 重复运行不覆盖已改进结果（scaffold 默认不覆盖非 in_progress 版本）。

生态贡献
- [ ] SKILL.md、scripts、references 齐备；依赖/路径/限制写清；最小示例标注为示例。

## 6. 降级策略

- 缺材料正文：status=partial/insufficient_evidence，只写已验证部分，limitations 写明缺口；不得补造正文。
- 缺软件（如无 pdftotext）：用标准库兜底读 txt/md；PDF 无文本层则记 limitations，status 相应降级。
- 材料冲突/歧义：保留多种解读写入 uncertainty，标 source_conflict / pending_human，不隐藏。
- 图表/公式缺失或不可读：quote 写 `<figure/table not extractable>`，verdict=pending_human，不猜内容。
- 数字/引用/术语无法回溯：不写入 deliverable；确需保留则记 uncertainty/pending_human。
- 严禁编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。

## 7. 脚本与资源索引

- `scripts/main.py`：scaffold / extract / analyze / validate / finalize 五个子命令；缺省 `--input /input --output /app/output --work /app/work`。
- `scripts/evidence.py`：文本鲁棒读取、locator 索引、逐字回查、论断/证据模板。
- `scripts/selftest.py`：自检（正常/空目录/坏 JSON/quote 不一致四场景）。
- `references/evidence_rubric.md`：locator、quote、claim_kind、relation、strength、verdict、不确定性口径。
- `references/checklist.md`：提交前逐条自检清单。

最小调用示例（示例，非正式案例）：`python scripts/main.py scaffold && python scripts/main.py extract && python scripts/main.py finalize`。
