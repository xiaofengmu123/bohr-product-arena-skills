# 自检清单（逐条可勾选，对应评分维度）

交付前逐条核对。每条后的 `[维度]` 指向题目评分维度。失败项必须如实写入 result.json 的 `degradations` / `pending_human` / `errors`，不得隐藏。

## A. 实际效果（40 分）

- [ ] A1 覆盖完整性：`results` 覆盖 case.json 候选集的**全部条目**，无一遗漏；遗漏逐条写入 `omissions`。`[完整性]`
- [ ] A2 逐条判断：每条都有 `relevance`（direct/indirect/irrelevant/unknown），不是只给一部分。`[完成度]`
- [ ] A3 标签正确：`relevance` 与纳入/排除标准一致；标题关键词但对象/设计不匹配的未误判为 direct。`[正确性]`
- [ ] A4 元数据准确：title/authors/year/venue/doi/url 与输入一致，缺失填 null，不猜测。`[正确性]`
- [ ] A5 关键文献靠前：真正 direct 的核心文献排在 retain 区前部，不落在靠后位置。`[正确性]`
- [ ] A6 召回保持：indirect/关键文献未误排进 exclude；exclude 只放 irrelevant。`[完整性]`
- [ ] A7 去重处理：preprint vs published、同一研究多报道已归入 `dedupe_groups` 并裁定。`[完整性]`
- [ ] A8 可复核：每条决定有 `reasons` + `evidence`（locator + 逐字 quote）；打分口径、k 值、平局规则写清。`[可核验性]`

## B. 使用增益（25 分）

- [ ] B1 排序可用：retain → pending → exclude 组序稳定，priority_score 在组内单调合理。`[质量]`
- [ ] B2 降人工成本：`pending` 区尽量小，每条理由能让复核者快速判定，不需重读全文。`[效率]`
- [ ] B3 增益可验证：`change_log` 记录每一步改动与依据，他人可复现同一输入→同一排序。`[增益可验证]`

## C. 创新增益（15 分）

- [ ] C1 分层标签：retain/pending/exclude（或核心证据/方法参考/背景资料）三档完整落地，不是单一分数。`[新增能力]`
- [ ] C2 冲突裁定：标注分歧、摘要不足、版本重复、同一研究多报道均有明确规则并执行。`[方法改进]`
- [ ] C3 可解释排序：词面信号（scaffold）与语义判断（Agent）分离，最终排序由规则+证据支撑，不是黑箱。`[创新证据]`

## D. 可靠体验（10 分）

- [ ] D1 调用正确：按 SKILL.md 三步脚本可独立完成一次筛选，缺省参数指向 /input、/app/work、/app/output。`[易用性]`
- [ ] D2 稳定可比：相同输入两次运行，排序与标签一致（确定性平局键，无随机）。`[稳定性]`
- [ ] D3 异常识别：空候选、缺摘要、缺年份、标准冲突、大量同分、接口异常、权限不足均被识别并如实标注。`[异常处理]`
- [ ] D4 诚实降级：材料不足返回 `insufficient_evidence`；坏 JSON/坏编码/超大文件/缺软件写 `degradations`，不编造、不崩溃。`[异常处理]`

## E. 生态贡献（10 分）

- [ ] E1 文档完整：SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理、限制与维护信息。`[文档]`
- [ ] E2 资源可复用：`references/criteria.md`、`references/checklist.md`、三个脚本可被他人复用。`[复用]`
- [ ] E3 示例诚实：最小调用示例的示意输出明确标注“示例”，不冒充正式案例结果。`[分享]`

## 交付前命令（在 skill 根目录执行）

```bash
python3 -B scripts/discover.py --input /input --work /app/work --output /app/output
python3 -B scripts/screen.py   --work /app/work --output /app/output
# Agent 依据 materials 与 references/criteria.md 填写 result.json 的 results
python3 -B scripts/finalize.py --output /app/output --work /app/work
```

`finalize.py` 应打印 `errors=0`；如有 warning，逐条修复或如实记录原因后重跑。三个脚本在空输入、坏 JSON、缺参数、超大文件下都应退出码 0 并产出合法 result.json。