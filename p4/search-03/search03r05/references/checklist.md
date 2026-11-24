# 自检清单（references/checklist.md）

交付前逐条勾选；每条后的 `[维度]` 对应题目评分维度。失败项必须写进 result.json 的
`degradations` / `pending_human` / `errors`，不得隐藏。完整可机读校验由
`scripts/main.py finalize` 完成，本清单供人工逐条确认。

## A. 实际效果（40 分）

- [ ] A1 覆盖完整性：`results` 覆盖候选集**全部** `record_id`，无一遗漏；遗漏逐条写 `omissions`。`[完整性 5]`
- [ ] A2 逐条判断：每条都有 `relevance` ∈ {direct, indirect, irrelevant, unknown}，非只判一部分。`[完成度 15]`
- [ ] A3 标签正确：`relevance` 与纳排标准一致；标题含关键词但人群/设计不匹配的未被误判 direct。`[正确性 15]`
- [ ] A4 元数据准确：title/authors/year/venue/doi 与输入一致，缺失填 null，不猜测。`[正确性]`
- [ ] A5 关键文献靠前：direct 文献位于 retain 区前部，按 priority_score 稳定排序。`[正确性]`
- [ ] A6 召回保持：indirect/关键文献未误排进 exclude；exclude 只放 irrelevant。`[完整性]`
- [ ] A7 去重处理：预印本 vs 正式版、同研究多报道已入 `dedupe_groups` 并裁定。`[完整性]`
- [ ] A8 可复核：每条有 `reasons` + `evidence`（locator + 逐字 quote）；打分口径、平局键写清。`[可核验 5]`

## B. 使用增益（25 分）

- [ ] B1 排序可用：retain → pending → exclude 顺序稳定，priority_score 区内单调递减。`[质量 15]`
- [ ] B2 降人工成本：pending 区尽量小，每条理由使复核者无需重读全文即可判定。`[效率 5]`
- [ ] B3 增益可验证：`change_log` 记录每步 ts/step/action 与依据，同一输入可复现同一排序。`[可验证 5]`

## C. 创新增益（15 分）

- [ ] C1 分层标签：retain/pending/exclude 三档完整落地，非单一分数。`[新增能力 5]`
- [ ] C2 冲突裁定：分歧标注、摘要不足、版本重复、同研究多报道均有明确规则并执行。`[方法改进 5]`
- [ ] C3 可解释排序：词面信号（scaffold）与语义判断（Agent）分离，最终排序由规则+证据支撑。`[创新证据 5]`

## D. 可靠体验（10 分）

- [ ] D1 调用正确：按 SKILL.md 三步脚本可独立完成一次筛选（≤3 个脚本命令）。`[易用性 3]`
- [ ] D2 稳定可比：相同输入两次运行结果一致（固定平局键 + 确定性实现）。`[稳定性 4]`
- [ ] D3 异常识别：空候选、缺摘要/年份、标准冲突、大量同分、权限/接口异常均被识别并标注。`[异常 3]`
- [ ] D4 诚实降级：材料不足=insufficient_evidence；缺软件/缺数据写 degradations，不编造。

## E. 生态贡献（10 分）

- [ ] E1 文档完整：SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理、限制与维护。`[文档 3]`
- [ ] E2 资源可复用：criteria.md、checklist.md、三个脚本可被他人复用。`[复用 4]`
- [ ] E3 示例诚实：最小调用示例的示意输出明确标注"示例"，不冒充正式案例结果。`[分享 3]`

## 交付前命令（在 skill 根目录执行）

```bash
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py screen   --work /app/work --output /app/output
# Agent 依据 materials 与 references/criteria.md 填充 result.json 的 results
python3 -B scripts/main.py finalize --output /app/output --work /app/work
python3 -B scripts/main.py selftest
```

`finalize` 应输出 `errors=0`；selftest 应输出全部 PASS。warning 逐条修复或如实记录原因。