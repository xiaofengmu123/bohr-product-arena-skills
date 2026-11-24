# 证据强度与不确定性口径（evidence_rubric）

本文件是 SKILL.md 第 3 节「字段语义」的判定细则，供分析阶段逐条套用。所有取值用英文短名，便于结构化写入 result.json。

## 1. 论断切分（claim splitting）
- 一句话一个可判定陈述；复合句（因果、转折、并列）拆成多条。
- 每条字段：`claim_id`（C1、C2…）、`text`（改写后的可判定断言）、`type`（fact/number/term/method/interpretation）、`verb`（陈述动词）、`requires_citation`（bool）。
- 不把作者观点拆成无意义碎片，也不把多个事实并成一条。

## 2. 来源类型 source_kind
| 值 | 含义 |
|---|---|
| primary | 原始研究（实验/观察/临床/测量） |
| review | 综述/系统综述/meta 分析 |
| guideline | 指南/共识/标准 |
| preprint | 预印本（未经同行评议） |
| other | 教材/工具书/数据表/未归类 |
| unknown | 无法判定 |

## 3. 证据强度 evidence_grade
| 值 | 判定 | 典型情形 |
|---|---|---|
| direct | 原文直接支持该论断，设计与样本匹配 | 原始研究直接测得该效应，口径与论断一致 |
| partial | 支持相近但更弱/更强的版本 | 单篇小样本、间接指标、亚组、机制代理 |
| background | 仅背景性/综述/指南支持 | 综述转述、专家意见、预印本 |
| conflict | 至少两条来源方向相反 | 必须并列呈现，不下定论 |
| none | 未找到来源 | 标 pending_human 或删除该论断 |

## 4. 支撑方向 support
| 值 | 含义 |
|---|---|
| supports | 原文支持该论断，表述强度相当 |
| overstates | 论断比原文更强（原文是相关，写成了因果） |
| understates | 论断比原文更弱 |
| contradicts | 原文与论断矛盾 |
| not_found | 未找到对应原文 |

## 5. 不确定性表述（说得准）
- 相关性≠因果：原文为关联/相关时，正文用「与…相关」「呈关联」，不写「导致」「使」。
- 单篇小样本：用「初步表明/提示/观察到」，不写「证实」「领域共识」。
- 机制未明：明写「机制尚不明确」，不补造机制。
- 冲突证据：并列呈现双方，标注 source_conflict，结论用「尚存争议」。
- 强弱动词对应：established→表明/证实；preliminary→初步表明/有望；single-study→提示/观察到。

## 6. locator 规范
- 统一 1-based：`S{section}.P{para}.s{sentence}`；无法分节用 `P{para}.s{sentence}`；字符区间 `C{start}:{end}`（0-based 半开）。
- `source_doc` 给文件名；PDF 有页码时加 `page`；图表用 `fig{table}`/`tab{table}`。
- `quote` 必须逐字摘录，不可改写；长度按需，须含判断关键词。
