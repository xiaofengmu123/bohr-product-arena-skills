# 方法与检查清单（method-and-checklist）

本文件说明拆解、规划、扩展、收敛的具体规则与评分维度对应的检查清单。脚本不消费模型请求，只做确定性辅助。

## 1. 六要素拆解

把研究问题拆为 SQ1..SQn，每条子问题显式标注六要素：

- **对象（object）**：研究对象/群体/物质/系统。
- **机制（mechanism）**：作用机制、因果路径、效应端点。
- **方法（method）**：需要何种研究设计/测量（RCT、队列、机制实验、仿真等）。
- **时间范围（time_range）**：年份窗口或时点。
- **比较关系（comparison）**：与对照/基线/替代方案的比较。
- **证据要求（evidence_requirement）**：需要的证据类型或强度阈值。

子问题用 `{"id": "SQ1", "question": "…", "object": "…", "mechanism": "…", "method": "…", "time_range": "…", "comparison": "…", "evidence_requirement": "…"}` 记录。

## 2. 查询规划

为每个子问题安排查询顺序与依赖，用 `query_plan` 记录，每条含 `id`、`sub_question_ids`、`query`、`depends_on`、`parallel`（可并行）。先覆盖面查询建立候选池，再做缺口补充与冲突验证，避免相同查询重复执行。

## 3. 术语扩展

把关键概念扩展为同义词/缩写/跨语言/上下位/领域变体，**每概念 ≤6 变体**以控制发散。记录格式：`{"concept": "…", "variants": ["…"], "ambiguity": "…/null"}`。术语严重歧义时注明并加限定词，或拆成两条查询分别记录。

## 4. 透明打分

对每条 included 记录自动打分（脚本 `compute_score`）：

$$score = 0.6 \cdot relevance + 0.2 \cdot metadata\_completeness + 0.2 \cdot source\_reliability$$

- `relevance`：记录给的 0–1 值；`high/medium/low` 映射 1.0/0.6/0.3。
- `metadata_completeness`：title/authors/year/journal/doi 五项中已填比例。
- `source_reliability`：paper_search/Pubmed/Crossref/OpenAlex/arXiv/本地材料等为 1.0，其他有来源为 0.5，无来源 0.3。

按得分降序赋 `rank`。`score_breakdown` 逐项写明，保证可复核。

## 5. 停止判断

`stop_decision` 用 `status` 表达：

- `done`/`complete`/`converged` → result.json `status=ok`
- `handoff`/`blocked` → `status=partial`（需要人工）
- `no_evidence`/`exhausted` → `status=insufficient_evidence`
- 其他/未填 → `status=partial`

停止条件：每个子问题都有 ≥1 条 included 证据、冲突已双收并标注、边际收益低于继续检索的成本。过早收敛与无意义重复都要在 `evidence` 中给出理由。

## 6. 可靠性检查（scripts/selftest.py）

五个 in-process 场景，验证：空输入目录 → `insufficient_evidence`；坏 JSON → 记录 degradations 不崩溃；坏编码 → 容错降级；完整增量流程 + 契约回填 → `ok` 且契约字段齐备；重复 `run` 不覆盖已有工作。运行 `python3 -B scripts/selftest.py`，全部 PASS 即通过。

## 7. 评分维度对照

| 评分维度 | 对应产物 |
|-|-|
| 实际效果（完成度/正确性/完整性/可核验性） | `sub_questions` 六要素、`results` 真实字段与 locator+quote、`uncertainty.coverage_gaps`、`evidence`+`screening` 全量账本 |
| 使用增益 | 多轮规划、受控术语扩展、发票价全量筛选、透明打分、`selfcheck` 全 PASS |
| 创新增益 | 六要素拆解、依赖化查询规划、停止阈值、0.6/0.2/0.2 打分由脚本固化 |
| 可靠体验 | `--help` 退出 0、空/坏输入不崩溃、重复运行幂等、异常写 errors/degradations、selftest 全 PASS |
| 生态贡献 | SKILL.md + scripts + references 齐全，他人可按本说明复现 |

## 8. 复核一份结果的步骤

1. 打开 `result.json`，检查 `case_id` 与 `status` 是否诚实。
2. 逐条核对 `results` 的 `evidence.locator` 回到 `materials/` 原文，`quote` 逐字一致。
3. 打开 `screening.md`，确认所有候选记录都在账本中、排除项有理由。
4. 打开 `plan.md`，核对子问题、查询与停止理由能串成一条可复现路径。
5. 运行 `python3 -B scripts/main.py selfcheck --output /app/output --work /app/work`，应全 PASS。