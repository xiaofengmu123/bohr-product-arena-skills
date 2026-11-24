# 自检清单（对应评分维度）

交付前逐条勾选；`python3 -B scripts/pipeline.py selfcheck` 覆盖可自动化的部分，
`python3 -B scripts/selftest.py` 覆盖本包自身结构的自检。

## 实际效果（40 分）
- [ ] 从 /input 提取研究问题、纳入/排除标准，并落实到可判定判据（protocol.json 冻结）
- [ ] 每条唯一记录有 decision + reason_code（非 uncertain 时还有 evidence_quote）
- [ ] evidence_quote 在标题/摘要中逐字命中（finalize 校验，未命中写 degradations）
- [ ] 去重只合并同一研究；预印本与正式版、多来源导出已处理并保留 merge_reason
- [ ] 检索、去重、纳排、冲突、审计各环节都有产出，计数闭合
- [ ] `deduplication.clusters[*]` 证据可支撑独立复核

## 使用增益（25 分）
- [ ] 相同输入重复 `skeleton → normalize → finalize`，去重与计数一致
- [ ] `screening_table.csv` 为逐条结构化，减少人工逐条重查标题摘要
- [ ] 冲突显式列出并仲裁，不静默取一人意见

## 创新增益（15 分）
- [ ] 理由码体系完整（见 references/reason_codes.md），支持回溯重判
- [ ] 分歧自动分类（decision / reason_code / evidence）
- [ ] **契约回显**：result.json 字段与 output_contract 逐一对齐（contract_paths 校验）
- [ ] **change_log 追加式账本**：每环节时间戳 + before/after 计数，单调递增

## 可靠体验（10 分）
- [ ] 空输入目录 → `status=insufficient_evidence` 且退出码 0
- [ ] 坏 JSON / 坏编码 → 记录到 degradations/errors，不崩溃
- [ ] 缺参数 → 默认指向 /input、/app/output、/app/work
- [ ] DOI 缺失 / 摘要为空 / 标准冲突 / 标注者僵持均有处置路径
- [ ] `selfcheck` 全部 PASS

## 生态贡献（10 分）
- [ ] SKILL.md 写清用途、触发条件、输入输出、异常处理与时间预算
- [ ] 脚本只用标准库，路径相对，可直接复用到他人综述
- [ ] 协议模板、标准表、理由码字典、审计日志格式齐全

## 诚实性（一票否决项）
- [ ] 未编造 DOI、标题、数值、评测分数或运行结果
- [ ] 材料不足如实写 `insufficient_evidence` / `pending_human`，不做示例填充
- [ ] 示意输出（selftest 的 fixture）已明确标注为示例，不冒充正式案例结果
