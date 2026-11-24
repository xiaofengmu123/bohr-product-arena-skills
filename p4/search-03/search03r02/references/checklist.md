# 自检清单（checklist.md，逐条可勾选，对应评分维度）

交付前逐条核对。失败项必须在 `result.json` 的 `degradations` / `pending_human` /
`errors` 中如实说明，不得隐藏。括号内 `[维度]` 指向题目评分维度。

## A. 实际效果（40 分）

- [ ] A1 覆盖完整：`results` 覆盖 `case.json` 候选集**全部条目**；遗漏逐条写入
      `omissions`。`[结果完整性 5 分]`
- [ ] A2 逐条判断：每条都有 `relevance`（direct/indirect/irrelevant/unknown），
      不是只给一部分。`[任务完成度 15 分]`
- [ ] A3 标签正确：`relevance` 与纳排标准一致；标题命中但设计不匹配的
      **未**误判为 direct。`[结果正确性 15 分]`
- [ ] A4 元数据准确：title/authors/year/venue/doi 与输入一致，缺失填 null，
      不猜测。`[结果正确性]`
- [ ] A5 关键靠前：真正 `direct` 文献排在 retain 区前部，不落在 40 位之后。
      `[结果正确性]`
- [ ] A6 保召回：间接/关键文献未误排进 exclude；exclude 只放 irrelevant。
      `[结果完整性]`
- [ ] A7 去重处理：preprint vs published、同一研究多篇报道归入 `dedupe_groups`
      并裁定。`[结果完整性]`
- [ ] A8 可复核：每条决定有 `reasons` + `evidence`（locator + 逐字 quote）；
      打分口径、k 值与平局规则写清。`[结果可核验性 5 分]`

## B. 使用增益（25 分）

- [ ] B1 排序可用：retain → pending → exclude 顺序稳定，priority_score 单调合理。
      `[质量增益 15 分]`
- [ ] B2 降人工成本：`pending` 区尽量小，每条理由足以让复核者快速判定，
      不需重读全文。`[效率增益 5 分]`
- [ ] B3 增益可验证：`change_log` 记录每步改动与依据，他人可复现同一输入→
      同一排序。`[增益可验证性 5 分]`

## C. 创新增益（15 分）

- [ ] C1 分层标签：retain/pending/exclude 三档完整落地，不是单一分数。
      `[新增能力 5 分]`
- [ ] C2 冲突裁定：标注分歧、摘要不足、版本重复、同一研究多篇报道均有
      明确规则并执行。`[方法改进 5 分]`
- [ ] C3 可解释排序：词面信号（scaffold）与语义判断（Agent）分离，最终排序
      由规则+证据支撑，不是黑箱。`[创新证据 5 分]`

## D. 可靠体验（10 分）

- [ ] D1 调用正确：按 SKILL.md 三步脚本可独立完成一次筛选。`[易用性 3 分]`
- [ ] D2 稳定可比：相同输入两次运行结果一致（确定性平局键，无随机）。
      `[运行稳定性 4 分]`
- [ ] D3 异常识别：空候选、缺摘要、缺年份、标准冲突、大量同分、接口异常、
      权限不足均被识别并如实标注。`[异常处理 3 分]`
- [ ] D4 诚实降级：材料不足返回 `insufficient_evidence`；缺软件/缺数据写
      `degradations`，不编造。`[异常处理]`

## E. 生态贡献（10 分）

- [ ] E1 文档完整：SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理、
      限制与维护信息。`[文档完整性 3 分]`
- [ ] E2 资源可复用：criteria.md / checklist.md / 三个脚本可被他人复用。
      `[资源可复用性 4 分]`
- [ ] E3 示例诚实：最小调用示例的示意输出明确标注"示例"，不冒充正式案例结果。
      `[分享推广价值 3 分]`

## 交付前命令（在 skill 根目录执行）

```bash
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py screen   --work /app/work --output /app/output
# Agent 依据 materials 与 references/criteria.md 填写 result.json 的 results
python3 -B scripts/main.py finalize --work /app/work --output /app/output
python3 -B scripts/selftest.py        # 空输入 / 坏JSON / 确定性三场景自检
```

`finalize` 输出 `errors=0`；如有 warning，逐条修复或如实记录原因后重跑。
`selftest.py` 应输出 `selftest OK: 3/3 scenarios passed`。
