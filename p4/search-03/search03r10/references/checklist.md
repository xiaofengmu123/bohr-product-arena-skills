# 自检清单（逐条可勾选，对应评分维度）

交付前逐条核对；失败项必须落在 `result.json` 的 `degradations` / `pending_human` / `errors`
中如实说明，不得隐藏。括号内为评分维度映射。

## A. 实际效果（40 分）

- [ ] A1 覆盖完整：`results` 覆盖 `case_normalized.json` 候选集全部条目；遗漏逐条进 `omissions`。`[完整性]`
- [ ] A2 逐条判断：每个条目都有 `relevance`（direct/indirect/irrelevant/unknown）。`[完成度]`
- [ ] A3 标签正确：与纳排标准一致；标题关键词但对象/设计不匹配的未误判为 direct。`[正确性]`
- [ ] A4 元数据准确：title/authors/year/venue/doi 与输入一致，缺失填 null，不猜测。`[正确性]`
- [ ] A5 关键文献靠前：direct 文献在 retain 区前部，未沉底。`[正确性]`
- [ ] A6 召回保持：间接相关/关键文献未误进 exclude；exclude 只放 irrelevant。`[完整性]`
- [ ] A7 去重处理：preprint vs published、同一研究多报道已入 `dedupe_groups`/`conflicts` 并裁定。`[完整性]`
- [ ] A8 可复核：每条决定有 `reasons` + `evidence`（locator+quote）；打分公式、k、平局键写清。`[可核验性]`

## B. 使用增益（25 分）

- [ ] B1 排序可用：retain→pending→exclude 稳定，`priority_score` 单调合理。`[质量]`
- [ ] B2 降人工成本：pending 区尽量小，每条理由让复核者无需重读全文即可判定。`[效率]`
- [ ] B3 增益可验证：`change_log` 由脚本按步记录（discover/signal/merge/finalize），同输入→同排序可复现。`[增益可验证]`

## C. 创新增益（15 分）

- [ ] C1 分层标签：retain/pending/exclude 三档完整落地，非单一分数。`[新增能力]`
- [ ] C2 冲突裁定：分歧/摘要不足/版本重复/同一研究多报道均有规则并执行。`[方法改进]`
- [ ] C3 可解释排序：`score_components` 三因子分离，词面先验与语义判断分离，规则+证据支撑非黑箱。`[创新证据]`

## D. 可靠体验（10 分）

- [ ] D1 调用正确：按 `scripts/main.py` 四步 + 一次 selftest 可独立完成一次筛选。`[易用性]`
- [ ] D2 稳定可比：固定平局键 + 确定性 merge，重复运行标签与排序一致。`[稳定性]`
- [ ] D3 异常识别：空候选、缺摘要/年份/DOI、标准冲突、大量同分、接口异常、权限不足均识别并标注。`[异常处理]`
- [ ] D4 诚实降级：材料不足返回 `insufficient_evidence`；缺软件/数据写 `degradations`，不编造。`[异常处理]`

## E. 生态贡献（10 分）

- [ ] E1 文档完整：SKILL.md 含用途、适用条件、调用、输入输出、异常、限制与维护。`[文档]`
- [ ] E2 资源可复用：`references/criteria.md`、`checklist.md`、三个脚本可被他人复用。`[复用]`
- [ ] E3 示例诚实：最小调用示例的示意输出标注"示例"，不冒充正式案例结果。`[分享]`

## 交付前命令（在 skill 根目录执行）

```bash
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py signal   --work /app/work --output /app/output
# Agent 逐条判断，把每条决定以一行 JSON 追加到 /app/work/decisions.jsonl
python3 -B scripts/main.py merge    --work /app/work --output /app/output
python3 -B scripts/main.py finalize --work /app/work --output /app/output
python3 -B scripts/main.py selftest
```

`finalize` 应打印 `errors=0`；有 warning 逐条修复或如实记录后重跑。
