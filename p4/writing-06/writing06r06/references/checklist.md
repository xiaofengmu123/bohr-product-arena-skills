# 六维诊断执行清单（逐步可勾选）

执行时逐条勾选并记录。每勾选一项，都要在 result.json 的 finding/claim 中留下 locator+quote；无对应问题的维度写 `not_a_problem` 并给一句定位证据，避免"该查的没查"。

## D1 研究问题与贡献主张对齐
- [ ] 引言是否有一句可证伪的研究问题？定位问题句。
- [ ] 摘要/结论的贡献主张逐条列出，逐条回指方法/结果中实际完成的工作。
- [ ] 是否存在"首次/完全/普遍/彻底"等过度主张？逐条核对支撑范围。

## D2 章节结构与叙事顺序
- [ ] 用 chapter_model.json 的 chars 占比列出各章篇幅，标记明显失衡。
- [ ] 检查关键术语首次出现位置：是否在定义前被使用。
- [ ] 检查方法细节是否散落在结果/讨论（错位），结果是否被复述成讨论。

## D3 论证链与论据对应
- [ ] 对 claim_model 中每条 assertion/conclusion 句，找其后（或所指）的论据定位。
- [ ] 强因果/强推广结论：确认样本量、条件、测量是否够格，不够标 `weak`。
- [ ] 检查是否存在"A→C 无 B"跳跃或结论前提互为支撑的循环。

## D4 段落关系与重复内容
- [ ] 打开 redundancy.json，对每组近重复句判断：应合并还是保留强调，给动作。
- [ ] 逐段检查相邻两段是否有过渡或主题衔接，无则标 `insert_transition`。
- [ ] 检查是否一段含多个不同论点，应 `split`。

## D5 结论边界与表述尺度
- [ ] claim_model 中 causal 标记句逐条判断：证据是因果还是相关，相关则 `narrow`。
- [ ] 结论是否遗漏样本量/条件/测量/外部效度限制，缺则 `add_evidence` 或标注。
- [ ] 是否把特定对象/条件结论推广到全类，是则收窄措辞。

## D6 材料与格式异常
- [ ] 缺章节/缺参考文献/缺图表编号/公式缺失/稿件被截断 → 写入 `material_gaps`，不编造。
- [ ] PDF/图片不可直接读 → 用 pdf-text-tool / pdf-render-tool / ocr-tool，失败则标注 `not_reported`。

## 输出前自检（对应评分维度）
- [ ] 任务完成度：诊断 + 修改方案均为可编辑 Markdown，满足体裁/受众/格式。
- [ ] 正确性：每个 finding 都有 locator+quote，不虚构缺陷、不漏真实断裂。
- [ ] 完整性：六维均有结论（含 not_a_problem 定位证据）。
- [ ] 可核验性：source_map 每条有 locator+quote，claim_map 每条可回溯。
- [ ] 版本边界：diffs、pending_human、material_gaps 齐全，未确认处明确标注。
- [ ] 异常诚实：缺数据/缺软件写进 uncertainty/limitations，status 如实，不编造。
