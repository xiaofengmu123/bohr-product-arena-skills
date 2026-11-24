# 方法与检查清单（references/method-and-checklist.md）

本文件说明本 Skill 的方法取舍、打分规则、阶段预算账本与逐条检查清单，供评审或第三方按此独立复现一次完整检索。

## 1. 六要素拆解

把研究问题拆成 `SQ1..SQn`，每条必须标注六要素：对象、机制、方法、时间范围、比较、证据要求。目的：避免把复杂问题整体套成关键词；让每个子问题都能被一条可判定"找到证据/没找到证据"的查询覆盖。缺失要素写 `none` / `no_restriction`，不省略字段。

## 2. 依赖化查询规划

为子问题安排 `step`，只允许 `depends_on` 指向更小 step（避免成环），并把互不依赖的步骤标 `parallel_with`。顺序建议：先对象+机制（高区分度），再方法，最后时间/比较约束。这样能减少无效检索，且每个 step 的 `rationale` 可被独立复核。

## 3. 受控术语扩展

对每个概念列出同义词、缩写、跨语言表达、上下位与领域变体，但强制 `expansion_limit ≤ 6`，防止发散。术语有歧义时在 `disambiguation` 写清限定词，或拆成两条查询分别记录，绝不混在一句查询里。

## 4. 多轮检索与停止条件

- 第 1 轮按 `query_plan` 执行；后续轮次据上一轮结果补缺口、验证冲突证据。
- 每轮结束把新增候选写入 `records_in.json`，把查询/筛选动作写入 `evidence_in.json`。
- 停止判断三选一：`converged`（每个子问题均有 included 证据且无新冲突）、`marginal`（边际收益过低，新查询无新增有效记录）、`handoff`（时间/请求将超预算或需人工判断）。状态写进 `stop_decision.status` 与 `reason`。

## 5. 透明打分与排序

`assemble` 用固定公式对每条记录打分并排名，Agent 不手填分数：

```
score = 0.5 * relevance + 0.3 * metadata_completeness + 0.2 * source_reliability
```

- `relevance` ∈ [0,1]：Agent 判断，必须伴随 locator + quote。
- `metadata_completeness`：title(0.30) + authors(0.20) + year(0.20) + journal(0.15) + doi_or_url(0.15)。
- `source_reliability`：有 DOI=1.0；paper_search/Crossref/OpenAlex/PubMed/Scopus/WoS/arXiv=0.8；web_search=0.5；有 URL 无库=0.6；未知=0.0。
- 排名：include 在前、uncertain 在后，按 score 降序；score 相同时按 relevance、metadata、title 依次降序。`relevance` 缺失者排最后并标 uncertain。

## 6. 阶段预算账本（总 900 秒 / 64 次模型请求）

**铁律**：第 0 步 `scaffold` 必须在开始后 3 分钟内落盘 `status=partial` 的合法 `result.json`；此后只用 `set-plan` / `import` / `evidence` / `assemble` 增量更新，禁止覆盖计划。

| 阶段 | 请求预算 | 时间预算 | 硬截止 | 完成标志 |
|-|-|-|-|-|
| 0 scaffold | 0 | ≤3 分钟 | 3 分钟 | partial result.json 已存在且合法 |
| 1 拆解 | 6–8 | 4 分钟 | 10 分钟 | set-plan(sub_questions) 完成 |
| 2 规划 | 4–6 | 3 分钟 | 15 分钟 | set-plan(query_plan) 完成 |
| 3 扩展 | 4–6 | 3 分钟 | 20 分钟 | set-plan(term_expansion) 完成 |
| 4 检索 | 24–30 | 25 分钟 | 45 分钟 | import(候选) + evidence 逐轮 |
| 5 筛选 | 12–16 | 10 分钟 | 55 分钟 | 全量账本完成 |
| 6 收敛 | 4–6 | 3 分钟 | 60 分钟 | set-plan(stop_decision) 完成 |
| 7 组装 | 0 | ≤1 分钟 | — | assemble 刷新产物 |
| 8 自检 | 0 | ≤1 分钟 | — | selfcheck 全 PASS |

请求将超预算时：立即固化 `stop_decision.status=handoff`，`pending_human` 列缺口，`assemble` 固化 partial 结果；**宁可诚实降级，不赶工编造**。

## 7. 降级与诚实原则

- 候选为空 → `status=insufficient_evidence`，写明缺失与下一步建议，不补造文献。
- 缺软件/接口异常/限流/权限不足 → `errors`/`degradations`，改读本地 `materials/` 并标注局限。
- 元数据缺失 → `null`/`not_reported` + `missing_metadata`；冲突 → 全部记录 + `source_conflict`。
- 无法判断 → `pending_human`，不猜测。

## 8. 检查清单（逐条可勾选）

- [ ] 研究问题拆成 ≥1 条子问题，每条六要素齐全。
- [ ] 每条 included 结果的标题/作者/年份/期刊/DOI/URL 真实且逐字保留来源写法。
- [ ] 每条结果与子问题真正相关，相关性依据为 locator + 逐字 quote。
- [ ] 每个子问题有 ≥1 条 included 证据；缺口写入 `uncertainty.coverage_gaps`。
- [ ] `evidence` 记录每轮查询、筛选、排序、停止理由；`screening` 覆盖全部候选。
- [ ] `scripts/selftest.py` 与 `scripts/main.py selfcheck` 全部 PASS。
- [ ] 空输入 / 坏 JSON / 坏编码不崩溃且退出码 0；同输入重复运行确定。
- [ ] 他人可按本文件与 `references/result-schema.md` 独立复现一次完整检索。

## 9. 复现步骤

```bash
cd /skills/weekly-skill
python3 -B scripts/selftest.py                    # 作者自检（含空输入/坏JSON/确定性）
python3 -B scripts/main.py --help                 # 退出码 0
# 对每个正式案例：
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# 读 case_summary.json → 拆解/规划/扩展 → set-plan；检索 → import/evidence；收敛 → set-plan
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
```
