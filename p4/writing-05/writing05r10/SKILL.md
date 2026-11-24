---
name: evidence-claim-writing
description: 文献证据驱动写作 Skill。把科研材料拆成可独立判定的论断单元，逐条抽取带定位的原文逐字片段并匹配来源，判定支撑范围与证据强度、控制不确定性表述，产出"论断清单—证据表—成稿"三段可核验交付。用于要求"找得到、对得上、说得准、复得现"的学术写作、改写、校对、引用核验与证据强度校准任务。
version: 1.0.0
---

# 文献证据驱动写作（evidence-claim-writing）

## 1. 角色与何时使用
本 Skill 把"依据文献的写作"变成可核验的三段交付：论断清单 → 证据表 → 成稿。核心不是多写，而是让每句有依据的话都能指认依据在哪、依据有多强。
适用：需要文献支撑的写作/改写/校对；引用核验；论断—来源关联；证据强度与不确定性表达；要求保留来源与修改记录的任务。
不适用（如实说明并退出）：无材料正文、纯格式/排版、要求整篇重写且明确不做证据审计。

## 2. 输入契约
运行时原始输入固定在 `/input/`：
- `/input/case.json`：任务说明、材料路径或内嵌内容、输入约束、`output_contract` 字段、评分证据范围。
- `/input/task.md`：题目要求。
- 其他材料（稿件 PDF/txt/md、图表、数据）按 case.json 提供；PDF 只有原文，无预解析答案层。
工作目录 `/app/work/`；最终产物写 `/app/output/`。
铁律：以实际读到的文件内容为准，不按文件名/标签补造；材料缺失、冲突、异常分别记录。

## 3. 输出契约
必须产出 `/app/output/result.json`，至少含：
- `case_id`：取自 case.json（`case_id` > `caseId` > `id`）。
- 回显 case.json 中 `output_contract`/`outputContract`/`contract`/`result_schema` 的每个键（缺值补 `null`/`[]`，不改名、不删未知键）；缺失时按默认契约补 `deliverable`（目标产物）、`sources`（事实与来源映射）、`change_log`（版本与边界）。
- 另加：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`；建议 `limitations`、`errors`、`degradations`、`runtime`。

字段语义：
- `status`：`complete | partial | insufficient_evidence | input_error | failed`；缺关键材料不得写 `complete`。
- `deliverable`：主结果 = `claims`（论断清单）+ `evidence`（证据表）+ `draft`（成稿/改稿文本或路径）+ `summary`。每条论断含 `id`、`statement`、`locator`、`quote`、`source_ids`、`support`（direct/indirect/none/conflict）、`strength`、`uncertainty`、`status`（ok/needs_edit/missing_source/overclaim/conflict）、`action`。
- `sources`：逐条 `{key, value, kind, location, source_doc, change}`，数字/术语/引用/图表/关键论断均可回溯，不新增材料外事实。
- `uncertainty`：判断不确定、证据不足、需人工确认处；`pending_human`：必须由人决断的项。
- `change_log`：`[{time, action, detail}]`，记录 scaffold/analyze/audit/render/finalize。

locator 统一 `S{section}.P{para}.s{sentence}`；无法分节用 `P{para}` 或字符区间。每条判断可指回原文复核。
另写同源可读版 `/app/output/evidence_writing.md`（不新增事实）。

## 4. 执行流程（总预算 900s / 64 次模型请求，每次响应 ≤16384 tokens）
各阶段都写 partial，绝不因追求完美而重跑丢结果。
- P0 读题+骨架（0–3 min，≤3 请求）。读 task.md + case.json + 目录清单；运行 `python scripts/main.py --scaffold` 落盘 result.json 骨架（status=in_progress）并初始化 `/app/work/claim_graph.json`。**硬性要求：前 3 分钟内必须先落盘骨架，此后只做增量改进。**
- P1 取料+拆论断（3–10 min，≤18 请求）。读材料全文；PDF 用 `pdftotext -layout <pdf> /app/work/manuscript.txt`，再读全文。逐句拆出实质论断，抽逐字 quote 与 locator，写入 `claim_graph.json`（中间表示 IR）。
- P2 匹配+定级（10–13.5 min，≤25 请求）。为每条论断匹配来源、判定 support 范围与 evidence strength、施加不确定性限定；更新 `claim_graph.json`。
- P3 校验+成稿（13.5–15 min，≤10 请求）。先校验 IR（quote 逐字、locator 可解析、来源可解析、强度合法、overclaim 预警），再渲染成稿与证据表；运行 `python scripts/main.py --finalize` 写终版 result.json + evidence_writing.md。
- 预留 ~6 请求 / 60s 处理重试；任一步失败先落盘 partial 并标注。
脚本以相对路径从 Skill 根执行；cwd 非 Skill 根时用 `/skills/weekly-skill/scripts/xxx.py`。

## 5. 自检清单（对应评分维度，逐条可勾选）
实际效果
- [ ] 三段交付齐备：论断清单、证据表、成稿；体裁/受众/格式符合 case 约束。
- [ ] 每条论断有 id+locator+逐字 quote；quote 与原文一致，无虚构。
- [ ] 每条论断支撑范围（direct/indirect/none/conflict）已判定；引用真实存在、著录准确。
- [ ] 每条论断有证据强度定级，表述与定级相称（相关不写成因果、单篇不写成共识）。
- [ ] 每条判断可独立复核（id+locator+quote+source_doc 齐全）。
使用增益
- [ ] 同材料比"通用写作建议"更具体：每条 action 是明确编辑动作，不是写作原则。
- [ ] 一次给全四要素（论断/来源/强度/不确定性），减少返工轮次。
- [ ] 同稿同口径记录在 sources/change_log，可支撑增益。
创新增益
- [ ] 含论断切分、来源匹配、强度分级、不确定性控制至少之一的结构化产物。
- [ ] claim_graph IR + 校验前置（先校验后渲染）+ core_impl/main 模块拆分可复用。
可靠体验
- [ ] 缺章节/仅摘要/无参考文献/图表公式缺失/格式混乱/截断均识别并写入 limitations。
- [ ] 重复运行不覆盖已改进结果（scaffold 不覆盖非 in_progress 版本）。
- [ ] 空目录/坏 JSON/坏编码不崩溃，写合法结果并记 errors/degradations。
生态贡献
- [ ] SKILL.md、scripts、references 齐备；依赖/路径/限制写清；示例标注为示例。
运行 `python scripts/selftest.py` 自动跑上述部分自检并打印 PASS/FAIL。

## 6. 降级策略
- 缺材料正文：status=insufficient_evidence，只写已验证部分，limitations 写明缺口；不补造正文。
- 缺软件（如无 pdftotext）：用标准库兜底读取；仍失败则记录 limitations，status=partial/failed。
- 材料冲突/歧义：保留多种解读写 uncertainty，标 pending_human。
- 图表/公式不可读：不猜内容，记 limitations，可写"该处有公式/图但无法读取"。
- 数字/引用/术语无法回溯：不写进 deliverable；确需保留则记 uncertainty。
- 引用无法核验真实性：metadata_status=unverified，不冒充已核验。
- 严禁编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。

## 7. 边界
一个 Skill 只做"证据驱动写作"这一类任务；不内置正式测试答案、不生成白板；最小调用示例须标注为示例。

## 8. 最小调用示例（示例，非正式案例）
```bash
python3 scripts/main.py --scaffold --input /input --output /app/output --work /app/work
# Agent 阅读材料后编辑 /app/work/claim_graph.json（IR）
python3 scripts/main.py --finalize --output /app/output --work /app/work
python3 scripts/selftest.py
```
示意输出（标明为示例）：result.json 含 case_id/status/deliverable/sources/uncertainty/pending_human/change_log；evidence_writing.md 为"论断清单—证据表—成稿"三段可读版。
