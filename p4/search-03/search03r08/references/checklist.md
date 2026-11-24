# 自检清单（checklist）

交付前逐条核对。每条后的 `[维度]` 指向题目评分维度，`[步骤]` 指向 SKILL.md 第 4 节的执行步骤，`[字段]` 指向 result.json 中承载该证据的字段。失败项必须写入 result.json 的 `degradations` / `pending_human` / `errors` 并如实说明，不得隐藏。

## A. 实际效果（40 分）

- [ ] A1 覆盖完整性：`results` 覆盖 case.json 候选集的**全部条目**，无一遗漏；遗漏逐条写入 `omissions`。`[完整性] [步骤1/2] [字段:results,omissions]`
- [ ] A2 逐条判断：每个条目都有 `relevance`（direct/indirect/irrelevant/unknown），不是只给一部分。`[完成度] [步骤4] [字段:results[].relevance]`
- [ ] A3 标签正确：`relevance` 与纳入/排除标准一致；标题关键词但设计不匹配的未误判为 direct。`[正确性] [步骤4] [字段:results[].criteria_matched]`
- [ ] A4 元数据准确：title/authors/year/venue/doi 与输入一致，缺失填 null，不猜测。`[正确性] [步骤1/4] [字段:results[].title 等]`
- [ ] A5 关键文献靠前：真正相关的 `direct` 文献排在 retain 区前部，不落在第 40 位之后。`[正确性] [步骤5/6] [字段:results[].rank]`
- [ ] A6 召回保持：间接相关/关键文献未误排进 exclude；exclude 只放 irrelevant。`[完整性] [步骤4] [字段:results[].tier]`
- [ ] A7 去重处理：preprint vs published、同一研究多篇报道已归入 `dedupe_groups` 并裁定。`[完整性] [步骤5] [字段:dedupe_groups,results[].dedupe]`
- [ ] A8 可复核：每条决定有 `reasons` + `evidence`（locator + 逐字 quote）；打分口径、k 值、平局规则写清。`[可核验性] [步骤4/5/6] [字段:results[].reasons/evidence]`

## B. 使用增益（25 分）

- [ ] B1 排序可用：retain → pending → exclude 顺序稳定，priority_score 单调合理。`[质量] [步骤5/6] [字段:results[].tier/rank/priority_score]`
- [ ] B2 降人工成本：`pending` 区尽量小，每条理由能让复核者快速判定，不需重读全文。`[效率] [步骤4] [字段:results[].reasons]`
- [ ] B3 增益可验证：`change_log` 记录每一步改动与依据，他人可复现同一输入→同一排序。`[增益可验证] [步骤7] [字段:change_log,runtime]`

## C. 创新增益（15 分）

- [ ] C1 分层标签：retain/pending/exclude（或核心证据/方法参考/背景资料）三档完整落地，不是单一分数。`[新增能力] [步骤4] [字段:results[].tier]`
- [ ] C2 冲突裁定：标注分歧、摘要不足、版本重复、同一研究多报道均有明确规则并执行。`[方法改进] [步骤5] [字段:results[].conflict,dedupe_groups]`
- [ ] C3 可解释排序：词面信号（scaffold）与语义判断（Agent）分离，最终排序由规则+证据支撑，不是黑箱。`[创新证据] [步骤3/4/6] [字段:results[].sort_key,scaffold.base_score]`

## D. 可靠体验（10 分）

- [ ] D1 调用正确：按 SKILL.md 的 discover/screen/finalize 三步可独立完成一次筛选。`[易用性] [步骤1/3/6] [字段:—]`
- [ ] D2 稳定可比：相同输入两次运行，排序与标签一致（确定性平局键 + 固定逻辑）。`[稳定性] [步骤6] [字段:results[].sort_key]`
- [ ] D3 异常识别：空候选、缺摘要、缺年份、标准冲突、大量同分、接口异常、权限不足均被识别并如实标注。`[异常处理] [步骤2/6] [字段:errors,degradations,uncertainty]`
- [ ] D4 诚实降级：材料不足返回 `insufficient_evidence`；缺软件/缺数据写 `degradations`，不编造。`[异常处理] [步骤2/6] [字段:status,degradations,pending_human]`

## E. 生态贡献（10 分）

- [ ] E1 文档完整：SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理、限制与维护信息。`[文档] [步骤0] [字段:—]`
- [ ] E2 资源可复用：`references/criteria.md`、`references/checklist.md`、三个脚本可被他人复用。`[复用] [步骤0/6] [字段:sources]`
- [ ] E3 示例诚实：最小调用示例的示意输出明确标注"示例"，不冒充正式案例结果。`[分享] [步骤7] [字段:—]`

## 交付前命令（在 skill 根目录执行）

```bash
python3 -B scripts/main.py --cmd discover  --input /input --work /app/work --output /app/output
python3 -B scripts/main.py --cmd screen    --work /app/work --output /app/output
# Agent 依据 materials 与 references/criteria.md 填写 result.json 的 results
python3 -B scripts/main.py --cmd finalize  --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

`finalize` 的 `selfcheck.json` 应 `errors_count=0`；如有 warning，逐条修复或如实记录原因后重跑。`selftest.py` 退出码 0 表示合成夹具全流程通过。

## 评分维度 → 自检项 → 流程 → 结果字段 对齐总表

本表把题目五个评分维度逐条映射到本清单的自检项、SKILL.md 的流程步骤与 result.json 的承载字段；`finalize` 会把同一映射写入 result.json 的 `dimension_audit`，使「哪个维度由哪一步、哪个字段证明」可被独立核对。

| 评分维度（分值） | 自检项 | 主要流程步骤 | 主要承载字段 |
|-|-|-|-|
| 实际效果（40） | A1–A8 | discover / screen / judge / dedupe / finalize | results、relevance、tier、evidence、dedupe_groups、omissions |
| 使用增益（25） | B1–B3 | judge / finalize | results[].rank/priority_score/tier、change_log、uncertainty |
| 创新增益（15） | C1–C3 | screen / judge / dedupe | tier、conflict、dedupe、criteria_matched、sort_key、scaffold.base_score |
| 可靠体验（10） | D1–D4 | discover / finalize / selftest | status、errors、degradations、uncertainty、runtime |
| 生态贡献（10） | E1–E3 | 全程文档与 selftest | sources、runtime、change_log、dimension_audit、SKILL.md |

自检时的核对顺序：先跑 `selftest.py` 确认脚本契约成立（D1/D4/E2），再对正式案例逐条核对 A/B/C 的语义判断，最后跑 `finalize` 确认 `errors_count=0` 与 `dimension_audit` 完整。
