# 自检清单（对应评分维度，交付前逐条勾选）

脚本 `python3 -B scripts/selftest.py` 覆盖其中可自动化的部分；其余靠人工核验。

## 实际效果（40 分）
- [ ] 从 /input 提取研究问题、纳入/排除标准，并写成带 `id` 的可判定规则（criteria_rules）
- [ ] 每条唯一记录都有 decision + reason_code（非 uncertain 还有逐字 evidence_quote）
- [ ] evidence_quote 在该记录标题/摘要中逐字命中（screen 阶段校验，未命中写入 degradations）
- [ ] 去重只合并“同一研究”副本；预印本与正式版、不同来源导出均保留 merge_reason
- [ ] 检索、去重、纳排、冲突、审计各环节均有产出，计数闭合（audit.closure=true）
- [ ] `deduplication.clusters[*].evidence` 含 DOI/标题证据，可供独立复核

## 使用增益（25 分）
- [ ] 相同输入重复运行 discover→contract→ingest→dedup→finalize，计数一致（确定性）
- [ ] 判定表为逐条结构化（screening_table.csv），减少人工逐条重查
- [ ] 冲突被显式列出并仲裁（conflicts 数组），而非静默取一人意见

## 创新增益（15 分）
- [ ] 理由码体系完整（见 references/criteria_rules.md），支持回溯重判
- [ ] 判据规则引擎：标准带版本号、逐条回指 rule_ids，标准漂移可追溯
- [ ] 审计链 = 追加式 ledger.jsonl + 计数闭合，第三人可复跑（append-only，不改写历史）

## 可靠体验（10 分）
- [ ] 空输入目录 → status=insufficient_evidence 且退出码 0
- [ ] 坏 JSON / 坏编码 → 写入 errors/degradations，不崩溃
- [ ] 缺参数 → 默认 /input、/app/output、/app/work
- [ ] DOI 缺失 / 摘要为空 / 标准冲突 / 标注者僵持均有明确处置路径
- [ ] selftest 全部 PASS

## 生态贡献（10 分）
- [ ] SKILL.md 写清用途、触发条件、输入输出、异常处理与时间预算
- [ ] 脚本只用标准库、相对路径，可复用到他人的综述
- [ ] 协议模板、标准规则、理由码字典、审计日志格式齐全

## 诚实性（一票否决）
- [ ] 未编造 DOI、标题、数值、评测分数或运行结果
- [ ] 材料不足时如实写 insufficient_evidence / pending_human，不做示例填充
- [ ] 示意输出（如有）已标注为示例，不冒充正式案例结果
