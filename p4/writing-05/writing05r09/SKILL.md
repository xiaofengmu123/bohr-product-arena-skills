---
name: evidence-grounded-writing
description: 文献证据驱动写作 Skill。把给定科研材料拆成可判定论断清单，为每条论断匹配到具体文献的具体段落（locator+逐字 quote），标注来源类型、证据强度与支撑方向，按强度控制不确定性表述，产出「论断清单—证据表—成稿」三段交付及可逐条回溯的 sources/change_log。用于需要文献支撑、引用核对、证据分级或不确定性控制的学术写作任务；须保留来源、不新增材料外事实、不得虚构缺陷时使用。
version: 1.1.0
---

# 文献证据驱动写作（evidence-grounded-writing）

## 1. 角色与何时使用
本 Skill 面向「证据驱动」的学术写作：交付的不是只有成稿，而是「论断清单—证据表—成稿」三段产物，使每句有依据的话都能指认依据在哪里、依据有多强。

适用：依据固定科研材料完成学术写作、改写、引用格式或结构化写作，且要求保留来源与修改记录。
不适用（如实说明并退出）：纯语言润色且明确不要求溯源；无任何材料正文；要求整篇自由创作而非依据材料。

核心四问（逐一回答，缺一即标 uncertainty）：
- 找得到：每条实质性论断被提取并关联到具体段落；
- 对得上：来源真实、元数据准确、原文确实支持该论断（不是相近但更弱/更强的话）；
- 说得准：证据强度与不确定性如实表达，相关性≠因果、单篇小样本≠共识、冲突不隐藏；
- 复得现：论断编号、原文摘录、定位与强度理由可被独立回查重跑。

## 2. 输入契约
运行时固定：`/input/case.json`（任务说明、材料路径、输入约束、可能含 `output_contract`）、`/input/task.md`（题目要求）；材料（PDF/txt/md/图表/数据）按 case.json 提供；PDF 只有原文，无预解析答案层。工作目录 `/app/work/`，最终产物 `/app/output/`。
铁律：以实际读到的文件为准，不按标签/文件名补造内容；材料缺失、冲突、异常分别记录。

## 3. 输出契约
必须产出 `/app/output/result.json`：
- `case_id`：取自 case.json（`case_id` > `caseId` > `id`）。
- 题目 `output_contract` 全部字段：优先原样回显 case.json 中 `output_contract`/`outputContract`/`contract`/`result_schema` 的每个键（缺值给 `null`/`[]`，不改名不删键）；缺失时按默认三件套 `deliverable`（目标产物）、`sources`（事实与来源映射）、`change_log`（版本与边界）补齐。
- 必须再加：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`；强烈建议 `limitations`、`degradations`、`errors`。

字段语义：
- `status`：`complete`|`partial`|`failed`；缺数据不得写 `complete`。
- `deliverable`：`{claims, evidence_table, manuscript, summary}`；claims 每条含 `claim_id/text/type/verb/requires_citation`；evidence_table 每条含 `claim_id/source_doc/locator/quote/source_kind/evidence_grade/support/grade_reason`；manuscript 为成稿文本，句中标注 claim_id。
- `sources`：逐条 `{key,value,kind,location,source_doc,change}`，数字/术语/引用/图表/关键论断均可回溯。
- `uncertainty`：判断不确定、证据不足、需人工确认处（逐条给原因）。
- `pending_human`：须人工确认/补充的项目。
- `change_log`：`[{time,action,detail}]`，记录 scaffold/analyze/grade/finalize。
- `limitations`：材料缺口、缺软件、截断、异常、未确认项。

locator 统一格式（1-based）：`S{section}.P{para}.s{sentence}`；无法分节用 `P{para}.s{sentence}`；字符区间 `C{start}:{end}`；`source_doc` 给文件名，PDF 有页码时加 `page`。证据强度口径见 `references/evidence_rubric.md`。另写同源可读版 `/app/output/result.md`（不新增事实）。

## 4. 执行流程（总 900s / 64 次请求 / 单次 16384 tokens）
- P0 读题+骨架（0–3 min，≤3 请求）：读 task.md + case.json + 目录清单；运行 `python scripts/main.py scaffold --case /input/case.json --input /input --out /app/output --work /app/work` 落盘 result.json 骨架（`status=in_progress`）。**硬性：前 3 分钟必须先落盘一版骨架，此后只增量改进，绝不因重跑丢失已改进结果。**
- P1 取料+分析（3–12 min，≤20 请求）：运行 `python scripts/main.py extract --input /input --work /app/work` 抽取材料全文（PDF 用 `pdftotext -layout`，失败则记录）；整篇通读原文，不用摘要替代；在 `/app/work/analysis.json` 写入论断清单与候选来源。
- P2 证据匹配+分级（12–13.5 min，≤25 请求）：逐条论断回填 source_doc/locator/quote，判定 source_kind、evidence_grade、support 与 grade_reason；缺项写「材料不足」，不虚构。
- P3 组装+校验（13.5–15 min，≤10 请求）：写终版 result.json，运行 `python scripts/main.py finalize --case /input/case.json --work /app/work --out /app/output` 校验字段、回显契约键、补 change_log、生成 result.md；再运行 `python scripts/selftest.py --input /input --output /app/output` 自检。
- 预留 ~6 请求 / 60s 处理重试；任一步失败先落盘 partial 并标注。
脚本从 Skill 根目录以相对路径调用；若 cwd 非 Skill 根，用 `/skills/weekly-skill/scripts/main.py`。

## 5. 自检清单（对应评分维度）
实际效果
- [ ] 三段交付齐备：论断清单 / 证据表 / 成稿。
- [ ] 每条实质论断有 claim_id 且成稿中可定位；无虚构、无遗漏真实断裂。
- [ ] 每条 citation 真实存在、元数据准确、quote 逐字一致且确实支持该论断。
- [ ] 每条论断标注 source_kind、evidence_grade、support 与 grade_reason。
- [ ] 每条判断可独立复核（locator + quote + source_doc 齐全）。
使用增益
- [ ] 同材料比「通用改写」更可执行、更少人工返工（回原文核对/补引用/改过度推断）。
- [ ] 强度分级+不确定性表述一次给全，减少返工轮次。
- [ ] 同稿同口径记录在 sources/change_log，可支撑增益对比。
创新增益
- [ ] 含论断—来源匹配、证据强度分级、不确定性控制至少之一的结构级产物。
- [ ] main.py + rubric + locator 规范可独立复用。
可靠体验
- [ ] 缺全文/仅摘要/摘要与原文矛盾/证据冲突/格式不支持/截断均写入 limitations 或 uncertainty。
- [ ] 重复运行不覆盖已改进结果（scaffold 默认不覆盖非 in_progress 版本）。
生态贡献
- [ ] SKILL.md、scripts、references 齐备；依赖/路径/限制写清；示例标注为示例。

## 6. 降级策略
- 缺材料文本：`status=partial`，只写已验证部分，limitations 写明缺口；不补造正文。
- 缺软件（如无 pdftotext）：标准库兜底读 txt/md；仍失败则 limitations 记录，`status=partial/failed`。
- 材料冲突/歧义：保留多解写入 uncertainty，标 source_conflict，待人工确认。
- 图表/公式不可读：不猜内容，记 limitations（「该处有公式/图但无法读取」）。
- 数字/术语/引用无法回溯：不写入 deliverable；确需保留则记 uncertainty/pending_human。
- 严禁编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。

## 7. 文件清单
- `SKILL.md`：本说明；
- `scripts/main.py`：scaffold/extract/finalize 三命令（仅标准库，<30KB）；
- `scripts/selftest.py`：空输入/坏 JSON/缺输入三种自检 + 产物合法性；
- `references/evidence_rubric.md`：论断切分、证据强度、支撑方向与不确定性口径；
- `references/checklist.md`：逐条可勾选检查清单与最小调用示例（示例非正式案例）。
