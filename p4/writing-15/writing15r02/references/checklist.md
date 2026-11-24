# 长文档写作检查清单

供 Agent 在 Phase 2/3 逐项核对；每项对应评分维度中的一个考察点。打勾项应有实际产物或 result.json 字段支撑，不允许以自述替代证据。

## A. 立得住（任务完成度 / 结果完整性）

- [ ] 已识别任务体裁、受众、文体与格式约束（来自 case.json / task.md，逐字引用关键句）。
- [ ] 提纲要求的所有章节均已产出，顺序与论证链对应；未要求的章节不擅自添加。
- [ ] 每节写清“本节回答什么问题、需要哪些证据”，避免把材料堆成若干段通顺文字。
- [ ] 必要论证要素齐全：研究问题、方法、结果、讨论、局限；著录项齐全：图表、参考文献、附录。
- [ ] 篇幅分配与提纲/字数要求一致；超出或不足处已说明。

## B. 连得上（结果正确性 / 跨章节一致性 / 质量增益）

- [ ] 术语表、符号表、定义清单、结论清单已建立并贯穿全文。
- [ ] 同一概念无多种写法；缩写首次出现时定义，后续一致。
- [ ] 图/表/章节/公式编号连续、无重复、无断号；交叉引用“图 X/表 X/第 X 章”均有对应目标。
- [ ] 引用文献的论断与所引文献结论一致，未出现“引用存在但结论被改写”。
- [ ] 改动一处后，受影响段落已通过脚本 consistency 或人工核对定位。
- [ ] 运行过 `scripts/main.py consistency`，报告并入 result.json；不可判定项已写 pending_human。

## C. 改得动（可编辑性 / 效率增益）

- [ ] 主产物为可编辑格式（.md/.json/.csv；Office 另附 .docx/.pptx/.xlsx）。
- [ ] 每次修改都有 before/after/why，写入 change_log，作者可看懂改了什么、为什么改。
- [ ] 局部修改只动了需要动的地方，保留作者原意与既有表述。
- [ ] 修改可回退、可比较：保留原句与改写句差异（diff 或 before/after 并置）。

## D. 查得清（可核验性 / 异常处理）

- [ ] 每条关键论断、数字、术语、引用、图表均有 locator + 逐字 quote 写入 sources/evidence_map。
- [ ] 冲突信息不隐藏：同一指标多来源不一致时，两个都记录并标 `source_conflict`。
- [ ] 材料缺失、无法判断、运行失败分别写入 errors / degradations / pending_human。
- [ ] 不新增材料外事实；不虚构引用、DOI、数值；缺失处写 `not_reported` 或 pending_human。
- [ ] result.json 中 status 与实际完成度一致（ok/partial/insufficient_evidence/input_error）。

## E. 稳定与交付

- [ ] 空输入目录返回 `insufficient_evidence`（退出码 0，不抛异常）。
- [ ] 坏 JSON / 坏编码被捕获并记入 errors/degradations，其余材料继续处理。
- [ ] 同一输入重复运行结果一致（脚本确定性、固定遍历顺序、无随机数）。
- [ ] `scripts/main.py selfcheck` 退出码 0。
