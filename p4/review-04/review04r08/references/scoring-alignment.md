# 评分维度对齐表（本 Skill 的核心设计取舍）

本题满分 100，共五个维度。本 Skill 不做"大而全"的复述式审查，而是把每个评分点
**逐条映射到一段可执行流程 + 一条可勾选自检项 + 一份可复核的证据产物**。
执行时不背题、不预测答案，只按此表让"流程本身"保证评分点被覆盖。

## 维度一：实际效果（40 分）

| 评分点 | 分值 | 落地做法 | 证据产物 | 自检项 |
|---|---|---|---|---|
| 任务完成度 | 15 | 六维审查 + 主张—证据逐条对齐 + 推理链 + 跨章节承诺—交付对照，产出 findings/claims/commitments/argument_chains | result.json.review 四类数组 | S1、S2 |
| 结果正确性 | 15 | 每条 finding 带逐字 quote + evidence + speaker 判别；防误判守则（对照组/假设/转述不误报） | references/review-checklist.md 防误判守则 | S3 |
| 结果完整性 | 5 | 六维 coverage 全标注 + cross_section_consistency 第 7 维 | review.coverage（7 键） | S2 |
| 结果可核验性 | 5 | 定位到段落（p页 ¶P段落），quote 逐字，evidence 可独立复核 | finding.locator/quote/evidence | S4 |

## 维度二：使用增益（25 分）

| 评分点 | 分值 | 落地做法 | 证据产物 | 自检项 |
|---|---|---|---|---|
| 质量增益 | 15 | 主张清单先于问题清单，先抽取再核验，减少漏报；跨章节对照单独做 | claims[] + commitments[] | S1、S2 |
| 效率增益 | 5 | 定位到段落 + 逐字 quote，评审免通读回找；suggestion 落到具体句子 | finding.suggestion | S8 |
| 增益可验证性 | 5 | 增量写回 + change_log + selfcheck/selftest，可复跑 | change_log、selftest.py | S9 |

## 维度三：创新增益（15 分）

| 评分点 | 分值 | 落地做法 | 证据产物 | 自检项 |
|---|---|---|---|---|
| 新增能力 | 5 | 主张—证据对齐矩阵 + argument_chains 推理链（基础 Agent 通常只给摘要式感想） | argument_chains[] | S1、S2 |
| 方法改进 | 5 | 严重度三问（影响主结论/可修正/可复现）替代"一律标中"；speaker 三分 | finding.reasoning | S5 |
| 创新证据 | 5 | 确定性脚本 + selftest 四类异常用例 + 打分对齐表本文件 | selftest.py | S9 |

## 维度四：可靠体验（10 分）

| 评分点 | 分值 | 落地做法 | 证据产物 | 自检项 |
|---|---|---|---|---|
| 调用与易用性 | 3 | 三段式 CLI（scaffold/update/selfcheck）+ 相对路径 + 最小调用示例 | SKILL.md §6 | S6 |
| 运行稳定性 | 4 | 前 3 分钟先落盘 partial 骨架；900s/64 请求预算；确定性（ts 留空） | main.py scaffold | S6、S7 |
| 异常处理 | 3 | 空输入/坏 JSON/坏编码/PDF 失败/超长截断均有降级并记录 errors/degradations | result.json.errors/degradations | S7 |

## 维度五：生态贡献（10 分）

| 评分点 | 分值 | 落地做法 | 证据产物 | 自检项 |
|---|---|---|---|---|
| 文档完整性 | 3 | SKILL.md 写清角色/输入/输出/流程/自检/降级/示例 | SKILL.md | S6 |
| 资源可复用性 | 4 | 检查清单、评分对齐表、报告模板、三段式脚本均可独立复跑 | references/ + scripts/ | S9 |
| 分享推广价值 | 3 | 不预置答案、无随机性、可在他机复现 | selftest.py | S9 |

## 自检项编号（SKILL.md §5 同步引用）

- S1 真在审查而非摘要：每条 finding 有 locator/quote/evidence/reasoning/suggestion
- S2 六维 coverage 全标注 + 跨章节承诺—交付对照已完成
- S3 无对照组/明确假设/转述观点误判；speaker 已区分
- S4 定位到段落（p页 ¶P段落），quote 逐字可复核
- S5 严重度三问写入 reasoning，不是一律"中"
- S6 result.json 可解析，含 case_id + output_contract 全部键 + status/sources/uncertainty/change_log
- S7 空输入/坏 JSON/坏编码/PDF 失败/超长截断均有降级且记录 errors/degradations
- S8 建议落到具体句子（补什么证据、改哪句、如何降级结论强度）
- S9 selfcheck + selftest 通过；重复运行结果一致
