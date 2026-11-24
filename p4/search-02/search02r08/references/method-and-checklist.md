# 方法与检查清单（可独立复现一次完整检索）

本文件给出方法与复查清单的细则。脚本负责确定性部分；推理、相关性判断、查询调整由 Agent 按本方法执行。

## 1. 六要素拆解（问题拆解）

把科研问题拆成 1–n 条可检索子问题，每条必须标注六要素：

- `object`（研究对象）
- `mechanism`（机制/作用路径）
- `method`（方法/设计）
- `time_range`（时间范围）
- `comparison`（比较关系/对照组）
- `evidence_requirement`（证据要求：效应量、指标、置信标准等）

每条子问题给出 `required_evidence` 清单（需要找到的证据类型），供后续覆盖追踪。无法确定某要素时写 `not_specified`，不猜测。

## 2. 查询规划（依赖化）

- 为每个子问题安排查询 `query_plan`：`id / sub_question_ids / query / database / dependencies / order / status / rationale`。
- 前置查询先行（其结果是后续查询的输入），互不依赖的查询可并行；用 `dependencies` 显式声明依赖。
- 规划目标是减少无效查询，不是追求查询次数多。

## 3. 术语扩展（受控发散）

- 对关键概念扩展同义词、缩写、跨语言（中/英）、上下位、领域变体，写入 `term_expansion`。
- **每个概念 ≤6 个变体**，避免发散失控；歧义术语在 `note` 注明并加限定词或拆成两条查询。

## 4. 多轮检索与全量筛选

1. 按 `query_plan` 用 `paper_search` / `web_search` / 本地 `materials/` 执行查询。
2. **每轮返回的每条记录都要导入**（`import`），不只看 top-K，不预先丢弃。
3. 逐条给 `screening` 决策与 `screening_reason`：`included / excluded / uncertain / merged_duplicate`。
4. 对 `included` 记录给 `evidence`（locator + 逐字 quote）与 `relevance` 评分。
5. 重复导出合并、冲突保留并标记，二者都写进账本。
6. 每轮用 `evidence` 记录查询、结果数、缺口与冲突，形成可解释的迭代路径。

## 5. 停止条件（明确阈值）

`stop_decision.status` 取其一：

- `sufficient`：每个子问题都有 ≥1 条 included 证据，且新查询边际收益低。
- `continue`：仍有子问题缺证据，继续下一轮。
- `handoff`：无法在预算内补齐，转人工；`pending_human` 列出具体缺口。
- `max_budget`：达到 64 请求或 900 秒上限。

停止依据写入 `stop_decision.reason` 与 `marginal_gain`，不得以“搜不到就停”作为理由。

## 6. 复现一次完整检索的最小步骤

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
# 前 3 分钟内完成以上骨架；此后只做增量更新：
python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py set-meta --file /app/work/meta_in.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work \
  --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
```

## 7. 交付前双重自检

- 进程内自检：`python3 -B scripts/main.py selfcheck`，全 PASS 才算可交付。
- 独立自检：`python3 -B scripts/selftest.py`，覆盖空输入、坏 JSON、坏编码、超大文件、完整流程、幂等与未筛选检出，退出码 0。

`references/result-schema.md` 定义了状态文件与 result.json 的完整字段；两文件配合即可由他人独立复现。
