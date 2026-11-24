# 查询构造、关键词扩展、去重与打分规则

本文档是 `SKILL.md` 执行流程第 2–6 步的操作细则，供 Agent 在检索阶段直接执行，也是他人复现时的方法说明。

## 1. 问题拆解（aspects）

把研究问题拆成 2–6 个角度，每个角度必须有明确检索目的，避免空泛查询：

| 角度类型 | 示例 |
|-|-|
| 研究对象 | 疾病、人群、材料、系统 |
| 方法/技术 | XAI、图神经网络、CRISPR 筛选 |
| 应用/任务 | 分割、诊断、预后、合成 |
| 评价指标/证据 | 精度、生物标志物、机制通路 |
| 时间/范围边界 | 近 5 年、人/动物、体外/体内 |

每个角度产出 `aspect_id`（如 `a1`）与 `label`，写入 `plan-update`。

## 2. 查询规划（queries）

每条查询记录：`id / text / source / database / aspect_id / rationale / status`。查询按“从精到广、从单概念到组合”排序：

1. **单概念**：研究对象或方法本身，测基线召回。
2. **AND 组合**：方法 + 对象（如 `XAI AND medical imaging`），提 Precision。
3. **OR 扩展**：同义/缩写/上下位（如 `(explainable OR interpretable OR XAI) AND (medical imaging OR radiology)`），提 Recall。
4. **跨语言**：中文关键词与英文术语并查（如 `可解释人工智能` 与 `explainable AI`）。
5. **引文/精炼**：对高相关命中的 DOI 查同源引文或期刊页，补关键遗漏。

每条查询执行后，用 `evidence --inline` 记录 `query/source/database/rationale/hits_found/hits_kept/result_ids`。

## 3. 关键词扩展核验清单

- [ ] 同义词：interpretable ↔ explainable ↔ transparent
- [ ] 缩写与全称：XAI、GNN、MRI、LLM
- [ ] 上下位：medical imaging → CT/MRI/病理
- [ ] 跨语言：中/英术语并查
- [ ] 词形变化：单复数、动词/名词（detect/detection）
- [ ] 方法别名：deep learning ↔ neural network ↔ transformer

扩展原则：**加词提高 Recall，组合提高 Precision；先用扩展提高召回，再用组合与打分控制无关结果。**

## 4. 去重与来源核验

- 去重键：优先 `doi:<小写DOI>`；无 DOI 用 `title 规范化|第一作者|年份`。
- 规范化：标题去标点、统一小写；作者取首作者；年份取首个四位数字。
- 同键记录合并：保留字段更完整者，合并 `query_ids / sources / evidence / aspects`（去重保序）。
- 核验顺序：标题 → 作者 → 年份 → 期刊 → DOI；任一字段跨来源不一致，写入 `uncertainty.conflicts`，不擅自取舍。

## 5. 打分与排序

- `score = 0.5*relevance + 0.25*metadata + 0.25*source`，三项分别量化相关性、元数据完整度、来源可靠度。
- 排序键：`-score` → `year` 降序 → `title` 规范化升序，保证重复运行一致。
- 排序只给“相对依据”，不冒充权威排名；最终以 `relevance_reason` + `evidence` 供人复核。

## 6. 适用边界（方法创新边界）

- 本方法适用于**开放学术数据库的元数据级检索**，不负责单篇全文解析或引文网络分析。
- 覆盖矩阵的“命中”以“结果可归属到角度”为准，不代表该角度已穷尽；未命中/未执行角度一律写入 `coverage_gaps`，不隐藏。
- 元数据准确率受数据库响应质量约束；无法确认的字段标 `null` 并进 `uncertainty.missing_metadata`，不猜测。