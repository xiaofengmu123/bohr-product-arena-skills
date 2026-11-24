# 方法细则与检查清单（科研问题驱动检索）

> 本文把“拆解 → 规划 → 扩展 → 检索 → 筛选 → 停止 → 组装 → 自检”的方法与
> 判据写清楚，供 Agent 执行和评审者复现。核心目标不是检索次数，而是三件事：
> 拆成了哪些可检索子问题、每个子问题是否找到足够证据、过程能否解释与复现。

## 1. 问题拆解（六要素）

把研究问题拆成可独立检索的子问题，逐条标注六要素：

1. **对象/样本**：研究的是什么（疾病、种群、材料、系统）。
2. **机制/关系**：作用机制、因果链、通路、现象关系。
3. **方法/设计**：实验设计、测量手段、统计方法、建模方法。
4. **时间范围**：时间窗或文献年代限制（如 2018–2024）。
5. **比较关系**：对照组、基准、金标准、组间对比。
6. **证据要求**：研究类型（RCT/队列/机制/综述）、结局指标、样本量、置信度。

输出：`sub_questions[]`，每条 `id/facet/text/key_concepts/required_evidence`。缺失的要素在 `key_concepts` 或 `required_evidence` 显式写“未限制”，不假装已经分析。

## 2. 查询规划（依赖与并行）

- 先查**高信息量前置查询**（研究对象 + 机制），再查依赖其结果的下游查询。
- 相互独立的子问题写入同一 `parallel_group`，可并行检索。
- 每条查询写明 `purpose`、`query_template`、`source`、`depends_on`，保证“为什么这样查”可复核。

## 3. 术语扩展（受控发散）

- 扩展类型：synonym / abbreviation / full-form / cross-language / hypernym / hyponym / domain-variant。
- 每个概念变体 ≤6，防止查询爆炸；扩展必须写 `rationale`。
- 术语严重歧义：在 `rationale` 注明歧义并加限定词，或拆成两条查询分别记录。

## 4. 多轮检索与全量筛选

- 每轮记录到 `evidence.json`（query/source/database/rationale/round）。
- 每一条候选记录必须给出 include / exclude / uncertain 决定；未决定的一律 `uncertain`，**不得静默假设相关**。
- 排除项必须写 `screening_reason`；重复项（DOI/规范化标题）合并并记 `merged_duplicates`。
- 相关性依据必须是 `locator + quote`（逐字原文），不是空泛评语。

## 5. 停止判断

| 状态 | 含义 |
|---|---|
| `pending` | 尚未收敛，继续检索 |
| `sufficient` | 每个子问题都有 ≥1 条 included 证据，且无关键缺口 |
| `low_marginal_return` | 连续空转或新增相关低于阈值，边际收益过低 |
| `handoff` | 存在无法自动解决的缺口，转人工 |

阈值：每子问题 ≥1 条 included；每轮 ≥1 条新相关；连续空转 ≤2 轮。无法完成的子问题必须列入 `uncertainty.coverage_gaps` 与 `pending_human`。

## 6. 状态语义（result.json 的 status）

| 实情 | 应写 |
|---|---|
| 全部分解 + 每子问题有证据 + 停止判断收敛 | `ok` |
| 缺部分子问题证据 / 有 uncertain / 停止未收敛 | `partial` |
| 无候选或无有效证据 | `insufficient_evidence` |
| `case.json` 缺失或坏 JSON | `input_error` |

`status` 与缺口必须自洽：`partial` 时 `degradations`/`limitations` 要能解释缺什么。

## 7. 自检清单（提交前逐项过）

- [ ] `result.json` 可解析，`case_id` 与输入一致。
- [ ] `output_contract` 每个键都在 result.json 里，未获得的已按 null/[] 回填。
- [ ] `status` 与完成度一致，`degradations`/`errors` 能解释所有缺口。
- [ ] 每条 included 结果标题/作者/年份/期刊/DOI 真实且逐字保留来源写法。
- [ ] 每条结果与子问题真正相关，相关性依据为 locator + 逐字 quote。
- [ ] 每个子问题均有 ≥1 条 included 证据；未覆盖的写入 `uncertainty.coverage_gaps`。
- [ ] `screening` 覆盖全部候选，excluded/uncertain 有理由，重复项已合并。
- [ ] `evidence` 记录每轮查询、筛选、排序与停止理由。
- [ ] `python3 -B scripts/main.py --help` 退出码 0；`python3 -B scripts/selftest.py` 全 PASS。
- [ ] 空输入/坏 JSON/坏编码不崩溃且退出码 0；重复运行结果一致（幂等）。

## 8. 失败与降级

- 缺数据/空结果：`insufficient_evidence`，写 `status_notes`/`limitations`，不补造文献。
- 缺软件/接口异常/限流/权限不足：记 `errors`/`degradations`，改用本地材料并标注局限。
- 元数据缺失：`null` + `uncertainty.missing_metadata`；多来源不一致：全部保留 + `source_conflict`。
- 脚本无法运行时：手写合法但不完整的 `result.json`，据实写 status/errors，不伪造日志、引文、分数。

## 9. 最小调用示例（示意输出，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
# … Agent 拆解后 …
python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

`run` 的骨架输出 `status=partial` 是预期中间态，随后由 `set-plan/import/evidence/assemble` 增量升级为 `ok`/`partial`/`insufficient_evidence`，不是失败。
