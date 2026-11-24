# 方法细则与检查清单

本文件描述六要素拆解、依赖化查询规划、受控术语扩展、停止判据、去重与冲突处理的
具体方法，并给出交付前的离线自检清单。它面向两类读者：执行检索的 Agent（当作规则
内化）与复核检索的他人（当作验收标准）。

## 1. 六要素拆解

把原始问题拆成 `SQ1..SQn`，每条显式标注六要素，缺失项留空：

| 要素 | 含义 | 拆解提示 |
|-|-|-|
| object | 研究对象/对象物 | 是谁/什么（分子、通路、人群、器件） |
| mechanism | 机制/作用途径 | 通过什么机制、因果链 |
| method | 方法/证据类型 | 用什么方法、什么数据类型 |
| time_range | 时间范围 | 年份窗、随访期 |
| comparison | 比较关系 | 与谁比、基线/对照 |
| evidence_type | 证据要求 | 定量/定性、RCT/观察/机制 |

原则：每条子问题应是「一条查询能找、一条记录能答」的最小可检验单元；不要把多个
正交维度塞进同一子问题。

## 2. 依赖化查询规划

- 先做**前置查询**：定位对象/机制的基础证据；再做**并行查询**：时间窗、比较组、
  方法变体各自独立补证。
- 每个查询写 `step/query/sub_question_ids/depends_on/parallel_with/rationale`。
- 依赖闭环、无前置依赖的查询可并行；能并行的不串行，减少无效检索轮次。

## 3. 受控术语扩展

- 对每个核心概念扩展同义词、缩写、跨语言、上下位、领域变体，**每概念 ≤6 变体**，
  在 `scope_note` 写明为何保留该变体，防止发散失控。
- 严重歧义词在 `scope_note` 注明歧义并加限定词，或拆成两条查询分别记录。

## 4. 多轮检索与停止判据

- 每轮检索后按 `evidence` 日志记录 query/source/rationale；发现缺口就调查询补证，
  发现冲突就记录双方、不掩盖。
- 停止判据（`stop_decision`）：

| status | 触发条件 |
|-|-|
| `converged` | 每个子问题都有 ≥1 条 included 证据，且无 unresolved 冲突 |
| `partial` | 部分子问题覆盖，或存在 unresolved 冲突/uncertain 记录 |
| `insufficient` | 无任何可用记录 |
| `handoff` | 需要人工补充权限、专有数据库或缺失材料 |

- 边际收益判据：新一轮查询没有新增「改变 included 集」的记录，即停止检索，不重复
  换关键词刷同一个结果。

## 5. 去重与冲突处理

- **去重**：DOI 相同优先合并；无 DOI 用规范化标题指纹（去标点、小写、保留 CJK）。
  被合并记录不删除，进 `screening.merged_duplicates`，可追溯。
- **冲突**：同一指标多来源不一致、或观点相反时，双方逐字 quote 都保留，标记
  `source_conflict`；`resolved` 只能是 `unresolved`/`reconciled`/`preferred` 之一，
  无法判断写 `pending_human`，绝不擅自取舍。

## 6. 透明打分

`score = 0.6·relevance + 0.2·metadata_completeness + 0.2·source_reliability`。

- `relevance`：由 Agent 按子问题贴合度赋 0–1；
- `metadata_completeness`：7 个元数据字段非空占比（title/authors/year/journal/doi/url/database）；
- `source_reliability`：provided material=1.0，paper_search/Crossref/PubMed/arXiv/DOI=0.9，
  web_search=0.8，通用网页=0.7，未知=0.6。

权重与分层由脚本固化，评审可复现每一分。

## 7. 降级与不确定性（缺数据不编造）

| 场景 | 处置 |
|-|-|
| 空结果 | `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺口与下一步 |
| 坏 JSON/坏编码/超大文件 | 记录到 `errors`/`degradations`，继续处理其余材料 |
| 接口异常/限流/权限 | `errors` 记录，改用本地 materials 并标局限 |
| 元数据缺失 | 标 null + `uncertainty.missing_metadata` |
| 重复导出 | `duplicates` 记录合并对，原记录保留 |
| 观点冲突 | `conflicts` 双方保留，不取舍 |
| 无法完成 | `stop_decision.status=handoff` + `pending_human` 列缺口 |

## 8. 交付前离线自检

在非作者环境（未装第三方依赖）运行：

```bash
python3 -B scripts/selftest.py
python3 -B scripts/main.py --help
```

必须全 PASS 的六类场景：空输入、坏 JSON、坏编码、去重+合同回填的 happy path、
幂等 assemble、以及 `main.py --help` 退出码 0。

人工复核清单：

1. 每个 included 结果都有 locator + 逐字 quote；
2. 每个 excluded/uncertain 记录都有理由；
3. `coverage.coverage_scope` 声明有限快照，无全局召回措辞；
4. `change_log` 显示「先 run 骨架 → 再增量 set-plan/import/evidence」而非一次性覆盖；
5. `result.json` 顶部出现案例 `output_contract` 的全部字段，未知键未被删除。
