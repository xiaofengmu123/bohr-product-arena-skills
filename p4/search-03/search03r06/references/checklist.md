# 自检清单（checklist.md，逐条可勾选）

交付前逐条核对。每条后 `[维度]` 指向题目评分维度；失败项必须写入 `result.json` 的 `degradations` / `pending_human` / `errors`，不得隐藏。`finalize` 的 `selfcheck.json` 会输出 `errors` 与 `warnings`，交付前 `errors=0`。

## A. 实际效果（40 分）

- [ ] A1 覆盖完整性：`results` 覆盖候选集**全部条目**；遗漏逐条写入 `omissions`。`[结果完整性]`
- [ ] A2 逐条判断：每条都有 `relevance`（direct/indirect/irrelevant/unknown），不是只给一部分。`[任务完成度]`
- [ ] A3 标签正确：`relevance` 与纳排标准一致；标题含关键词但设计不匹配的未误判 direct。`[结果正确性]`
- [ ] A4 元数据准确：title/authors/year/venue/doi 与输入一致，缺失填 null，不猜测。`[结果正确性]`
- [ ] A5 关键文献靠前：direct 文献落在 retain 区前部，未落到后部。`[结果正确性]`
- [ ] A6 召回保持：indirect/关键文献未误排进 exclude；exclude 只放 irrelevant。`[结果完整性]`
- [ ] A7 去重处理：preprint vs published、同一研究多篇报道已入 `dedupe_groups` 并裁定。`[结果完整性]`
- [ ] A8 可复核：每条有 `reasons` + `evidence`（locator + 逐字 quote）；打分口径、k 值、平局规则写清。`[结果可核验性]`

## B. 使用增益（25 分）

- [ ] B1 排序可用：retain → pending → exclude 稳定；priority_score 档内单调合理。`[质量增益]`
- [ ] B2 降人工成本：pending 区尽量小；每条理由让复核者无需重读全文即可判定。`[效率增益]`
- [ ] B3 增益可验证：`change_log` 记录每步改动与依据，他人可复现同一输入→同一排序。`[增益可验证性]`

## C. 创新增益（15 分）

- [ ] C1 分层标签：retain/pending/exclude 三档完整落地，非单一分数。`[新增能力]`
- [ ] C2 冲突裁定：分歧、摘要不足、版本重复、同一研究多报道均有明确规则并执行。`[方法改进]`
- [ ] C3 可解释排序：`plan.json`（规则）× `scaffold.json`（词面先验）× `evidence_ledger.json`（语义证据）三者分离，最终排序由规则+证据支撑，非黑箱。`[创新证据]`

## D. 可靠体验（10 分）

- [ ] D1 调用正确：按 SKILL.md 三步脚本可独立完成一次筛选，`--help` 退出码 0。`[易用性]`
- [ ] D2 稳定可比：同一输入两次运行排序与标签一致（固定平局键）。`[运行稳定性]`
- [ ] D3 异常识别：空候选、缺摘要/年份、标准冲突、大量同分、接口/权限异常均被识别并标注。`[异常处理]`
- [ ] D4 诚实降级：材料不足返回 `insufficient_evidence`；缺软件/缺数据写 `degradations`，不编造。`[异常处理]`

## E. 生态贡献（10 分）

- [ ] E1 文档完整：SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理、限制与维护。`[文档完整性]`
- [ ] E2 资源可复用：`references/criteria.md`、`references/checklist.md`、三个脚本可被他人复用。`[资源可复用性]`
- [ ] E3 示例诚实：最小调用示例的示意输出明确标注"示例"，不冒充正式案例结果。`[分享推广价值]`

## 交付前命令（在 skill 根目录执行）

```bash
python3 -B scripts/main.py --help                # 退出码 0
python3 -B scripts/selftest.py                  # 25 项全过，退出码 0
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py scaffold --work /app/work --output /app/output
# Agent 依据 materials 与 references/criteria.md 填写 /app/work/evidence_ledger.json
python3 -B scripts/main.py finalize  --work /app/work --output /app/output
```

`finalize` 输出 `errors=0` 为交付底线；warning 逐条修复或如实记录原因后重跑。
