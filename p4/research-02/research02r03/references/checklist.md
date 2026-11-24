# 自检清单（可勾选，对应评分维度）

运行 `python3 -B scripts/selftest.py --input /input --output /app/output` 可自动执行本清单中标注了检查号（C01–C15）的条目；其余为需要人工/Agent 逐条确认的语义项。每条同时给出评分维度归属。

## A. 调用与易用性（评分维度：可靠体验·调用易用性，3 分）

- [ ] C01 `/input/case.json` 可被脚本发现；找不到时降级为 `input_error` 或如实说明。
- [ ] C02 `/app/output/result.json` 存在且为合法 JSON。
- [ ] C03 `result.json.case_id` 非空（来自 `case.json`，缺失用文件名）。
- [ ] C04 `status ∈ {ok, partial, insufficient_evidence, input_error}`。
- [ ] C05 题目 `output_contract` 的全部字段已在 result.json 中回填，键名与类型不变，缺值填 `null`/`[]`。
- [ ] C06 `status / sources / uncertainty / pending_human / change_log` 五个诚实字段齐全。
- [ ] 手动：`SKILL.md` 的调用步骤与实际脚本路径一致，`python3 -B scripts/main.py --help` 退出码为 0。

## B. 任务完成度（评分维度：实际效果·任务完成度，15 分）

- [ ] C07 六个主产物齐全：`review.md`、`screening_log.json`、`evidence_table.jsonl`、`themes.md`、`conflicts_and_gaps.md`、`manifest.json`。
- [ ] C08 筛选标准（population/include/exclude/dedup_rule）写在逐篇纳排决定**之前**，且 `defined_before_decisions=true`。
- [ ] 手动：筛选 → 主题归纳 → 证据组织 → 草稿四步均有独立产物，结论与研究问题、证据范围一致。
- [ ] 手动：`themes.md` 的主题从证据聚类生成，不套用"背景/方法/挑战/展望"通用框架。

## C. 结果正确性（评分维度：实际效果·结果正确性，15 分）

- [ ] C09 `evidence_table.jsonl` 每行含 `evidence_id/source_id/locator/quote/claim/strength/field`，strength ∈ 四级。
- [ ] C10 `review.md` 的每个 `[S#]` 都能解析回 evidence_table 的一行。
- [ ] C12 未发现占位 DOI、伪造 DOI、虚构数值标记。
- [ ] 手动：每行 `quote` 为原文逐字摘录，`claim` 与 quote 含义一致，未跨文档串值、未把相关写成因果。
- [ ] 手动：措辞与 strength 一致——established 用"表明/证实"，suggestive 用"提示/初步表明"，disputed 标"存在争议"，not_reported 用"未见报告"。
- [ ] 手动：文献元数据（标题、作者、年份、DOI）真实存在且与原文一致。

## D. 结果完整性（评分维度：实际效果·结果完整性，5 分）

- [ ] 手动：覆盖输入文献集的主要主题、方法路线、正反证据与结论分歧。
- [ ] 手动：结论冲突并列呈现（标 `disputed`），研究空白落到具体证据缺口而非套话。
- [ ] 手动：`conflicts_and_gaps.md` 的每项空白都能指出"哪些问题没有任何文献覆盖"。

## E. 结果可核验性（评分维度：实际效果·结果可核验性，5 分）

- [ ] C11 每条证据行的 `source_id` 可解析回 `result.json.sources` 或 `screening_log.json` 的来源清单。
- [ ] 手动：文献清单、筛选记录、证据表、引用定位、版本（`run_id`）可独立复核。
- [ ] 手动：每条关键论断都能从 `[S#]` → evidence 行 → `locator`+`quote` → 原文逐级回溯。

## F. 运行稳定性与确定性（评分维度：可靠体验·运行稳定性，4 分）

- [ ] C14 `manifest.json` 中记录的产物 SHA-256 与当前文件一致。
- [ ] C15（`--determinism`）同一输入两次运行 Phase 0 骨架，`result.json` 字节级一致。
- [ ] 手动：脚本只依赖 Python 标准库；重复运行结果一致，不依赖墙钟时间与随机数。

## G. 异常处理（评分维度：可靠体验·异常处理，3 分）

- [ ] C13 `status` 与产物完备度一致：`ok` 必须证据表非空；`insufficient_evidence` 必须列出缺口；`partial` 必须说明未完成项。
- [ ] 手动：缺全文→标 `abstract_only` 并降 strength；元数据不全→`not_reported`；两源不一致→都记录并标 `source_conflict`。
- [ ] 手动：空输入/坏 JSON/坏编码/缺 `pdftotext`/超 2MB 文件均有处置记录（`errors`/`degradations`），不崩溃、不编造。
