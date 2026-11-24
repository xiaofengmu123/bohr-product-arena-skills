# 自检清单（对应评分维度，交付前逐条勾选）

脚本 `python3 -B scripts/selftest.py` 覆盖其中的自动化部分；
`python3 -B scripts/pipeline.py selfcheck` 校验运行期产物。

## 实际效果（40 分）
- [ ] 已从 /input 提取研究问题与纳入/排除标准，并逐条落地为可判定判据（`plan.json`）
- [ ] 每条唯一记录都有 `decision` + `reason_code`（非 `uncertain` 还有 `evidence_quote`）
- [ ] `evidence_quote` 能在该记录标题/摘要中逐字命中（`finalize` 已校验，未命中写入 degradations）
- [ ] 去重只合并“同一研究”（同 DOI / 规范化标题一致）；预印本与正式版已处理并保留 `merge_reason`
- [ ] 检索、去重、纳排、冲突、审计各环节都有产出，计数闭合
- [ ] `deduplication.clusters[*].evidence` 能支撑独立复核

## 使用增益（25 分）
- [ ] 相同输入重复运行 `skeleton → normalize → finalize`，去重与计数一致（确定性）
- [ ] `screening_table.csv` 为逐条结构化判定表，减少人工逐条重查标题摘要
- [ ] 冲突被显式列出并仲裁，而非静默取一人意见

## 创新增益（15 分）
- [ ] 理由码体系完整（见 `references/reason_codes.md`），支持回溯重判
- [ ] 分歧自动分类（decision / reason_code / evidence）
- [ ] 中间表示链（plan → records → dedup → judgments → evidence_map）可让第三人从任一环节复跑
- [ ] 标准版本管理：`criteria_version` 冻结，漂移自动报 `criteria_drift`

## 可靠体验（10 分）
- [ ] 空输入目录 → `status=partial/insufficient_evidence` 且退出码 0
- [ ] 坏 JSON / 坏编码 → 记录到 `degradations`/`errors`，不崩溃
- [ ] 缺参数 → 默认指向 /input、/app/output、/app/work
- [ ] DOI 缺失 / 摘要为空 / 标准冲突 / 标注者僵持均有明确处置路径
- [ ] `selfcheck` 全部 PASS

## 生态贡献（10 分）
- [ ] SKILL.md 写清用途、触发条件、输入输出、异常处理与时间预算
- [ ] 脚本只用标准库，路径为相对路径，可直接复用到他人综述
- [ ] 协议模板、标准表、理由码字典、审计日志格式齐全

## 诚实性（一票否决项）
- [ ] 未编造 DOI、标题、数值、评测分数或运行结果
- [ ] 材料不足时如实写 `insufficient_evidence` / `pending_human`，不做示例填充
- [ ] 示意输出（如有）已明确标注为示例，不冒充正式案例结果
