# 自检清单（对应评分维度）

交付前逐条勾选；脚本 `python3 -B scripts/selftest.py` 覆盖包结构与降级烟测，
`python3 -B scripts/pipeline.py selfcheck` 覆盖 `result.json` 契约与计数闭合。

## 实际效果（40 分）
- [ ] 从 /input 提取了研究问题、纳入/排除标准，并落到可判定的判据上（`protocol.json` 已冻结版本）
- [ ] 每条唯一记录都有 `decision` + `reason_code`；非 `uncertain`/`pending_human` 还有 `evidence_quote`
- [ ] `evidence_quote` 能在该记录标题/摘要中逐字命中（`finalize` 校验，未命中写入 `degradations`）
- [ ] 去重只合并"同一研究"副本；预印本与正式版、多来源导出均处理并保留 `merge_reason`
- [ ] 检索、去重、纳排、冲突、审计各环节都有产出，计数闭合（`audit.counts.closure == "closed"`）
- [ ] `deduplication.clusters[*].evidence` 能支撑第三人独立复核

## 使用增益（25 分）
- [ ] 相同输入重复运行 `skeleton → normalize → finalize`，去重结果与计数一致（确定性）
- [ ] 判定表为逐条结构化 `screening_table.csv`，减少人工逐条重查标题摘要
- [ ] 冲突被显式列出并仲裁，而非静默取一人意见

## 创新增益（15 分）
- [ ] 理由码体系完整（见 `references/reason_codes.md`），支持回溯重判
- [ ] 分歧自动分类（decision / reason_code / evidence_quote 三类）
- [ ] 审计链（sources → locator → quote → count）可让第三人复跑
- [ ] 标准版本冻结 + `change_log` 记录，防止筛选途中标准漂移

## 可靠体验（10 分）
- [ ] 空输入目录 → `status=insufficient_evidence` 且退出码 0
- [ ] 坏 JSON / 坏编码 → 记录到 `degradations`，不崩溃
- [ ] 缺参数 → 默认指向 /input、/app/output、/app/work
- [ ] 超大文件（>2MB）→ 跳过并记录，不拖垮进程
- [ ] DOI 缺失 / 摘要为空 / 标准冲突 / 标注者僵持均有明确处置路径
- [ ] `pipeline.py selfcheck` 全部 PASS；`selftest.py` 全部 PASS

## 生态贡献（10 分）
- [ ] SKILL.md 写清用途、触发条件、输入输出、异常处理与时间预算
- [ ] 脚本只用标准库、路径相对、可直接复用到他人的综述
- [ ] 协议模板、标准表、理由码字典、审计日志格式齐全

## 诚实性（一票否决项）
- [ ] 未编造 DOI、标题、数值、评测分数或运行结果
- [ ] 材料不足时如实写 `insufficient_evidence` / `pending_human`，不做示例填充
- [ ] 示意输出（如有）已明确标注为示例，不冒充正式案例结果
