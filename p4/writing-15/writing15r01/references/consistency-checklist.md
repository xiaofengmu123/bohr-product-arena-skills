# 一致性检查清单（对应评分维度）

执行流程中逐项勾选；每条都对应 `result.json` 或 `consistency_report.md` 中的可核验字段。

## 1. 立得住（章节规划 ↔ 实际效果·完成度）

- [ ] 每个工单条目（`organizer-edit-workorder.json`）都映射到明确章节/段落，写进 `edit_plan.md`。
- [ ] 每节回答“研究问题/方法/结果/讨论/局限”中的哪一项，证据来自哪些材料。
- [ ] 篇幅与论证任务匹配：不把材料堆成若干段通顺文字，而是按论证链条组织。

## 2. 连得上（跨章节一致性 ↔ 正确性/使用增益）

- [ ] 术语统一：同一概念只用一个中文术语 + 一个符号；`facts.json.terms` 中的变体全部核对。
- [ ] 符号表与正文一致：`facts.json.numbers` 的单位统一（如 µm/μm、mL/ml）。
- [ ] 编号连续：`consistency_report.md` 中 `fig_table_numbering` 无重复/缺口。
- [ ] 交叉引用：正文“见图/表 N”的目标存在，`crossref` 无告警。
- [ ] 结论一致：`value_conflict` 中同指标不同数值已人工确认或修正。

## 3. 改得动（局部修改 ↔ 创新增益）

- [ ] 只改需要动的地方，保留作者原句与表述；`change_log` 每项含 `before`/`after` 逐字片段。
- [ ] 每次改动有 `reason` 与 `source`（工单条目/材料位置），可回退、可比较。
- [ ] 未改动段落原样保留，不重写整章。

## 4. 查得清（证据溯源 ↔ 可核验性/生态贡献）

- [ ] 每个关键论断/数字/引用有 `sources[].locator` + `quote`。
- [ ] 引用与文献列表一一对应；缺的标 `pending_human`，不补造 DOI/文献。
- [ ] `evidence_map.json` 与正文可逐条回溯。

## 5. 可靠体验（异常处理 ↔ 运行稳定性）

- [ ] 空输入/坏 JSON/坏编码/超长文件：不崩溃，`errors`/`degradations` 记录，`status` 诚实。
- [ ] 同一输入重复运行结果一致（脚本无随机项，JSON 键序稳定）。
- [ ] PDF 无解析层时用平台 pdf-text-tool，失败则记 `limitations`。

## 6. 输出契约核对

- [ ] `result.json` 含 `case_id` 与题目 `output_contract` 全部键（缺值 `null`）。
- [ ] 含 `status`/`sources`/`uncertainty`/`change_log` 及 `errors`/`degradations`/`pending_human`/`runtime`。
- [ ] 主产物可编辑（`.md`/`.docx`/`.pptx`/`.xlsx`），图形有 SVG + PDF/PNG 预览。

> 启发式检查只提示复核，不自动改写原文；最终一致性判断以人工/Agent 逐条核对为准。
