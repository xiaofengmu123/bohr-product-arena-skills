# 自检清单（对应评分维度）

交付前逐条勾选；`python3 -B scripts/main.py selfcheck` 覆盖其中可自动化部分，
`python3 -B scripts/selftest.py` 覆盖离线三组合成夹具。

## 实际效果（40 分）

- [ ] 从 /input 提取研究问题、纳入/排除标准，落到可判定判据，protocol 已冻结
- [ ] 每条唯一记录有 decision + reason_code；非 uncertain 有逐字 evidence_quote
- [ ] evidence_quote 在标题/摘要中逐字命中（finalize 校验，未命中写 degradations）
- [ ] 去重只合并同一研究（DOI/规范化标题），possible_duplicate 未擅自合并
- [ ] 检索、去重、纳排、冲突、审计各环节有产出，计数闭合
- [ ] deduplication.clusters[*] 保留 merge_reason 与成员，可独立复核

## 使用增益（25 分）

- [ ] 相同输入重复 skeleton→normalize→finalize 结果一致（确定性，无随机）
- [ ] screening_table.csv 逐条结构化，减少人工逐条重查
- [ ] 冲突显式列出并仲裁，不静默取一人意见

## 创新增益（15 分）

- [ ] 理由码体系完整（reason_codes.md），支持回溯重判
- [ ] 分歧自动分类（decision / reason_code / evidence）
- [ ] 审计链（sources→locator→quote→count）可让第三人复跑
- [ ] 时间预算账本（checkpoint.json）记录阶段预算、请求记账与超时降级

## 可靠体验（10 分）

- [ ] 空输入 → insufficient_evidence 且退出码 0
- [ ] 坏 JSON/坏编码 → degradations，不崩溃
- [ ] 缺参数 → 默认 /input、/app/output、/app/work
- [ ] DOI 缺失/摘要为空/标准冲突/标注者僵持有处置路径
- [ ] selftest.py 与 selfcheck 全部 PASS

## 生态贡献（10 分）

- [ ] SKILL.md 写清用途、触发、输入输出、异常处理与时间预算
- [ ] 脚本仅标准库、路径相对，可复用到他人综述
- [ ] 协议模板、理由码字典、审计日志格式齐全

## 诚实性（一票否决）

- [ ] 未编造 DOI、标题、数值、评测分数或运行结果
- [ ] 材料不足如实写 insufficient_evidence / pending_human，不做示例填充
- [ ] 示意输出已标注为示例，不冒充正式案例结果
