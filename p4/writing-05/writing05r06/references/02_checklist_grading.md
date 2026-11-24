# 检查清单、证据强度分级与降级口径

## 1. 证据强度分级与表述口径
| 等级 | 定义 | 允许正文口径 | 反例（需改写） |
|-|-|-|-|
| A 强证据 | 多篇独立原始研究一致 / 系统综述 / 荟萃分析 / 大样本高质量设计 / 领域共识 | 表明、证实、established | 无 |
| B 中等证据 | 单篇设计良好的原始研究或指南推荐 | 研究发现、报告、reported | 无 |
| C 弱证据 | 单篇小样本、临床前、病例报告、亚组分析 | 初步表明、提示、suggests | 直接写"证实/表明"（hedge_too_strong） |
| D 不足证据 | 专家意见、无原始数据的综述陈述、仅摘要 | 有观点认为、可能、proposed | 写成确定性结论 |
| U 无法核验 | 元数据无法确认、原文不可得 | 不得作确定性结论，记 pending_human | 当作已证实来源 |

规则（说得准）：
1. 相关性不写成因果；机制未明不写"因为…所以…"。
2. 单篇小样本不写成领域共识。
3. 冲突证据并列呈现并记 `conflict_hidden` 之外的两方，不隐藏。
4. 正文措辞与等级相称：脚本 `verify` 对强动词/弱限定词做确定性匹配检查。

## 2. uncertainty_flags（不确定性标记）口径
- `correlation_as_causation`：把相关表述写成因果
- `single_study_as_consensus`：单篇写成共识
- `sample_size_small`：样本量过小
- `conflict_hidden`：存在冲突证据但正文未呈现
- `mechanism_unclear`：机制未明
- `applicability_unclear`：适用人群/外推边界未明
- `abstract_only`：仅有摘要可用
- `metadata_unverified`：文献元数据未核验
- `secondary_citation`：二手转引
- `retracted_source`：来源被撤稿
- `version_diff`：存在版本差异

## 3. 逐条自检清单（对应评分维度）
实际效果（40）
- [ ] 三段交付齐全：论断清单、证据表、成稿正文互相链接（claim_id↔evidence_id）
- [ ] 每条实质论断有 source_doc + locator + quote，且 quote 逐字与原文一致（verify 通过）
- [ ] 引用真实存在、著录准确、原文确实支持该论断（support_verdict 非 no_support/contradicts）
- [ ] 证据等级与正文表述相称（verify 无 hedge_too_strong）
- [ ] 冲突、机制未明、适用边界均用 uncertainty_flags 标注，未隐藏

使用增益（25）
- [ ] 相比"通用建议"多了 claim 粒度来源映射与逐字引文，减少回原文核对
- [ ] 中间产物可独立复用：他人可拿 plan/claim_units/evidence_map 重跑 verify 与 finalize
- [ ] 同稿同版本同口径记录在 sources/change_log 中

创新增益（15）
- [ ] 含结构化中间表示（plan/claim_units/evidence_map）与脚本化 quote 逐字回验（确定性、可复现）
- [ ] 证据强度分级→表述口径映射 + 不确定性标记，防止弱证据写成强结论

可靠体验（10）
- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/截断均识别并写入 limitations
- [ ] 重复运行不覆盖非 in_progress 结果；同输入两次运行结果一致（确定性脚本）
- [ ] 空输入、坏 JSON、坏编码、缺软件均不崩溃，写入 errors/degradations

生态贡献（10）
- [ ] SKILL.md、scripts、references 齐备；依赖/路径/限制写清；最小示例标注为示例

## 4. 降级与失败处理口径
| 场景 | 处置 |
|-|-|
| 缺稿件正文 | status=partial，只写已核验部分，limitations 写明缺口；不补造正文 |
| 无 pdftotext | 标准库 zlib 兜底提取；仍失败记 degradations，status=partial/failed |
| 材料冲突/歧义 | 保留多种解读写入 uncertainty 与 pending_human |
| 图表/公式不可读 | 不猜内容，记 limitations："该处有图/公式但无法读取" |
| 数字/引用/术语无法回溯 | 不写入 deliverable；确需保留则降级 pending_human |
| 坏 JSON/坏编码/空目录 | 捕获记录 errors/degradations，继续处理其余材料 |
| 严禁 | 编造评测分数、运行结果、基准对比、隐藏测试；结果只来自本次实际运行 |
