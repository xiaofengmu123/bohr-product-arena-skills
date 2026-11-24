# 自检清单与评分维度对齐

本清单把题目 5 个评分维度逐条映射到流程步骤与自检项，交付前逐条勾选。
脚本 `python3 -B scripts/pipeline.py selfcheck` 覆盖其中可自动化的部分；
`python3 -B scripts/selftest.py` 覆盖空输入、坏 JSON、正常案例、确定性四类离线自检。

## 实际效果（40 分）

- [ ] 从 `/input` 提取研究问题、纳入/排除标准，并逐条落到**可判定的判据**上（`protocol.json` 的 `criteria[].decidable_test`）
- [ ] 每条唯一记录都有 `decision + reason_code`；`include/exclude` 另有逐字 `evidence_quote`
- [ ] `evidence_quote` 在该记录标题/摘要中逐字命中（`finalize` 校验，未命中写 `degradations`）
- [ ] 去重只合并“同一研究”副本：DOI 精确匹配、标题精确匹配；预印本/正式版、多来源导出均已处理并保留 `merge_reason`
- [ ] 相似标题（0.85–0.95）只标 `possible_duplicate`，不自动合并（脚本已保证）
- [ ] 检索、去重、纳排、冲突、审计各环节都有产出，计数闭合
- [ ] `deduplication.clusters[*].evidence` 支撑独立复核

## 使用增益（25 分）

- [ ] 相同输入重复运行 `skeleton → normalize → finalize`，去重结果与计数一致（确定性；`selftest.py` 已测）
- [ ] 逐条结构化判定表 `screening_table.csv`，减少人工逐条重查标题摘要
- [ ] 冲突被显式列出并仲裁（`conflicts` 数组），而非静默取一人意见
- [ ] 双人独立判定（`reviewer_a / reviewer_b`）+ 分歧分类 + 仲裁，提升双人一致率

## 创新增益（15 分）

- [ ] 理由码体系完整（见 `references/reason_codes.md`），支持回溯重判
- [ ] 分歧自动分类：`decision / reason_code / evidence` 三类
- [ ] 审计链（`sources → locator → quote → count`）可让第三人复跑
- [ ] **标准版本管理与回溯重判**：`criteria_version` 冻结 + `pipeline.py criteria-diff` 检出漂移记录
- [ ] **评分维度对齐**：本清单把 5 个评分维度逐条映射到流程与自检项（本版重点设计取舍）

## 可靠体验（10 分）

- [ ] 空输入目录 → `status=insufficient_evidence` 且退出码 0（`selftest.py` 已测）
- [ ] 坏 JSON / 坏编码 → 记录到 `degradations`，不崩溃（`selftest.py` 已测）
- [ ] 缺参数 → 默认指向 `/input`、`/app/output`、`/app/work`
- [ ] DOI 缺失 / 摘要为空 / 标准冲突 / 标注者僵持均有明确处置路径
- [ ] 单文本文件 >2MB 跳过并记录（`inventory.json` 中 `oversize=true`）
- [ ] `selfcheck` 全部 PASS

## 生态贡献（10 分）

- [ ] `SKILL.md` 写清用途、触发条件、输入输出、异常处理与时间预算
- [ ] 脚本只用标准库，路径为相对路径，可直接复用到他人的综述
- [ ] 协议模板、理由码字典、审计日志格式、判定表模板齐全

## 诚实性（一票否决项）

- [ ] 未编造 DOI、标题、数值、评测分数或运行结果
- [ ] 材料不足时如实写 `insufficient_evidence` / `pending_human`，不做示例填充
- [ ] 示意输出（如有）已明确标注为示例，不冒充正式案例结果
