# 自检清单、result.json 字段与降级

## 1. 逐条自检清单（对应评分维度）

- [ ] 任务完成度（15）：交付"章节稿 + 七要素对照说明"，而非写作建议/提纲/整篇模板；artifact.path 指向 /app/output 下真实可编辑文件。
- [ ] 结果正确性（15）：每个数字/术语/引用/图表/论断都有 fact_source_map 条目与 source_ref；unmapped/conflict 单独说明；"已完成"与"拟开展"分清。
- [ ] 结果完整性（5）：七要素齐全、厚度匹配、要素衔接；一致性矩阵无"目标 N 条/路线 M 条/指标另说"错位。
- [ ] 结果可核验性（5）：段落↔要素↔证据三级对应；sources 全、change_log 完整、来源清单含 SHA256。
- [ ] 质量增益（15）：四问（立得住/说得准/对得上/查得到）给出基于材料的可观察结论，而非泛泛"准确、完整"。
- [ ] 效率增益（5）：台账 + 自检减少返工；change_log 体现增量改进而非推倒重写。
- [ ] 增益可验证性（5）：结论基于材料证据与来源映射，不靠精选案例或自我夸奖。
- [ ] 新增能力（5）：把"准确/完整/可编辑/可复核"译为可观察判据；创新点只写与具体已有工作的可核验差异。
- [ ] 方法改进（5）：材料抽取、要素映射、一致性回检、引用核验、评审人视角自检有实质区分。
- [ ] 创新证据（5）：uncertainty/limitations 写清边界与失效条件（哪类材料可靠、哪类失效）。
- [ ] 调用易用（3）：新评分员按 SKILL.md 与 references 可独立跑通；脚本路径相对、仅标准库。
- [ ] 运行稳定（4）：空目录、坏 JSON、缺输入三种情况不崩溃；重复运行结果一致（固定顺序、确定性）。
- [ ] 异常处理（3）：材料缺失、指南不明、超长输入、引用无法核实、学科跨度过大、格式冲突均有识别与处置记录。
- [ ] 文档完整/可复用/分享价值（3+4+3）：SKILL.md + rules.md + checklist.md 齐全；七要素清单、四问判据、一致性矩阵可被他人直接复用。

## 2. result.json 字段说明

- case_id：来自 case.json（或 "unknown"），不得虚构。
- status：done / partial / failed / in_progress。时间不足或材料缺失一律 partial，禁止把未完成写成 done。
- artifact：目标产物。path 指向 /app/output 下可编辑文件；格式与描述写清；Office/slide/diagram 需另附真实渲染 PDF/PNG 预览，缺则如实标注。
- element_map：七要素到正文段落的映射。
- consistency_matrix：目标↔内容↔路线↔指标矩阵与 mismatches。
- fact_source_map：事实与来源映射数组（字段见 rules.md 第 6 节）。
- version_boundary：modification_diff（修改差异）、manual_confirm（需人工确认项）、material_gaps（材料缺口）、anomalies（异常）。
- sources：真实使用到的来源文件清单（路径、定位、类型、SHA256）。
- uncertainty：不确定项、推断项、缺数据项，逐条说明原因。
- pending_human：需人工确认/补齐的项。
- change_log：逐次修改记录（time/action/reason/impact）。

## 3. 降级与异常处理

- 缺数据/缺材料 → version_boundary.material_gaps 与 uncertainty 如实标注，status=partial；严禁补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ uncertainty 写明未能生成的项，保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分写入 material_gaps，不声称已全部核对。
- 模型拒答/截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 冲突/分歧 → 记录双方依据与判据，不擅自选一、不擅自改分。
- 空输入目录 → 写 insufficient_evidence（或 partial + material_gaps），退出码 0，不抛异常。
- 坏 JSON/坏编码 → 记录到 errors/degradations，继续处理其余材料。

## 4. 最小调用示例（示例，非正式案例）

输入：/input/case.json 含 challenge_id；/input/task.md 指定"写一份 XX 方向的立项依据章节稿（1500 字，附事实来源）"；/input/materials/ 含两篇 PDF。

调用（以 Skill 根目录为 cwd）：

```text
python scripts/main.py init
python scripts/main.py manifest
# 分段精读材料 -> 登记 fact_source_map
# 写 /app/output/manuscript.md 与 element_map.md -> 更新 result.json
python scripts/selftest.py
python scripts/main.py verify
```

示意输出（标明为示例）：result.json 中 status=done、artifact.path=manuscript.md、fact_source_map 含每条论断的 locator 与逐字 quote。
