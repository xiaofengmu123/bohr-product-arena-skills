# 输出契约与方法规范

本文档是 `scripts/coverage.py` 中 `CONTRACT` 常量、阈值与补检流程的权威说明。脚本以本文件为准；两者不一致时，以脚本源码中的常量为最终可执行版本，本文件负责解释“为什么”。

## 1. result.json 输出契约（25 个顶层键）

`coverage.py` 把契约声明为数据（`CONTRACT` 字典），每个键带 `kind`（str/enum/list/dict）、`required` 与 `default`。`validate` 子命令逐键校验，任何缺失或类型错误都会列出来，而不是靠文字约定。

| 键 | 类型 | 含义 |
|---|---|---|
| `case_id` | str | 取 `case.json` 的 `case_id/id/challenge_id/armiId`；取不到用内容哈希（`hash-` 前缀），不虚构 |
| `status` | enum | `ok / partial / insufficient_evidence / input_error` |
| `output_contract` | dict | 若 `case.json` 有 `output_contract`，**逐字回填**；否则写入由运行时契约推导的字段清单（`source=runtime_contract`） |
| `research_question` | str | 研究问题；缺省 `not_reported` |
| `search_scope` | dict | 检索范围（数据库/时间窗/字段/过滤器） |
| `reference_set` | dict | 冻结参考集：`version`、`built_utc`、`records`、`deduplication_issues` |
| `search_sessions` | list | 每次检索会话：query/source_files/time_window/result_count |
| `coverage` | dict | `denominator`、`numerator`、`coverage_rate`、`omission_rate`、`hit_rule` |
| `hit_matrix` | list | 逐条 `ref_id/status(hit\|miss\|adjudicate\|excluded)/matched_by/candidate_*` |
| `omissions` | list | 逐条漏检：`ref_id/category/signals/evidence/query|source|time_window_attribution` |
| `supplementary_searches` | list | 补检：`search_id/query/source/new_hits/added_count` |
| `marginal_gain` | dict | `before_omissions/after_omissions/reduction/new_hits/denominator` |
| `residual_omissions` | list | 残余遗漏：`ref_id/reason/recoverable/action` |
| `search_results` | dict | **运行时契约·检索结果**：候选、元数据、来源定位 |
| `evidence` | dict | **运行时契约·证据记录**：查询/筛选/排序/追踪依据 |
| `status_report` | dict | **运行时契约·状态说明**：空结果/冲突/权限/接口异常/已完成/未完成 |
| `evidence_map` | list | 每条 `locator + quote + conclusion` |
| `sources` | list | 输入文件清单（路径、角色、字节数、记录数） |
| `uncertainty` | list | 不确定性逐条说明 |
| `pending_human` | list | 需人工裁决/补充的事项 |
| `change_log` | list | 每次写盘一条：`revision/time_utc/action/fields/note`，revision 单调递增 |
| `errors` | list | 输入/解析错误（不崩溃，记录后继续） |
| `degradations` | list | 降级处理记录（坏编码、超限文件等） |
| `limitations` | list | 诚实声明：未完成部分与原因 |
| `runtime` | dict | `start_utc/end_utc/elapsed_seconds/files_processed/files_skipped` |

**关键设计**：`search_results` / `evidence` / `status_report` 三个键直接对应题目“Skill 运行后的输出要求”的三行（检索结果、证据记录、状态说明）。无论 `case.json` 是否显式给出 `output_contract`，这三个区域都会被填充，使产物与运行时契约逐一对齐。

## 2. 命中判定规则（三级，确定性、可复算）

1. **标识符精确匹配**：`normalize_doi()` 后完全相等 → `hit`，`matched_by=identifier_exact`。
2. **标题 + 第一作者 + 年份模糊匹配**：标题 `SequenceMatcher` 相似度 ≥ `TITLE_FUZZY_HIT=0.88`，且第一作者（规范化后）相等或互为子串，且 `|年份差| ≤ YEAR_TOLERANCE=1`（缺作者或年份时不据此否决）→ `hit`，`matched_by=title_author_year_fuzzy`。
3. **人工裁决带**：标题相似度 ∈ `[TITLE_ADJUDICATION=0.70, 0.88)` 且无三级命中 → `adjudicate`，记录 `similarity`。
4. 其余 → `miss`，进入 `omissions`。

分母**只来自独立冻结参考集**，绝不来自检索结果自身；命中数 = `hit_matrix` 中 `status=hit` 的条数，分子分母均可手工复算。

## 3. 漏检分类（可操作、落得下去）

未命中项必须归到具体类别，每类给证据。常用类别：

- `query_synonym_gap`：查询词未覆盖同义表达（给出“原查询式”与缺失词）
- `field_too_narrow`：检索字段限制过窄（如只查标题未查摘要）
- `time_window_truncation`：时间窗截断（给出窗口与文献年份）
- `source_not_indexed`：来源库未收录（给出库名与证据）
- `identifier_anomaly`：记录本身标识异常（缺 DOI/错标题/撤稿/重复）

归因必须落到“哪一条查询式 / 哪个字段 / 哪个来源 / 哪个时间窗”，禁止只写“数据库覆盖不足”。脚本把未归因项标 `category=unattributed`，由 Agent 在 `merge --updates` 中补全。

## 4. 补充检索与停止规则

- 补检只统计**材料中真实存在、且补检前未命中**的新增记录；新增命中须带 `evidence_locator + quote`。
- `marginal_gain` 以补检前漏检数为分母：`reduction = 新增命中数`，`after_omissions = before_omissions - reduction`。
- 停止规则：若一次补检 `added_count == 0` 或连续两次无新增，停止并接受残余遗漏；残余遗漏逐条给 `recoverable + action`。
- 禁止“换同义词重跑无新增却报下降”——脚本按 `new_hits` 数量而非措辞计算 `reduction`。

## 5. merge --updates 文件格式

```json
{
  "adjudications":      [{"ref_id":"...","verdict":"hit|miss","note":"..."}],
  "omission_updates":   [{"ref_id":"...","category":"...","signals":[],"evidence":[],
                          "query_attribution":"...","source_attribution":"...",
                          "time_window_attribution":"..."}],
  "supplementary_searches":[{"search_id":"...","query":"...","source":"...",
                          "time_window":null,"new_hits":[{"ref_id":"...",
                          "evidence_locator":"...","quote":"..."}],"added_count":1}],
  "residual_omissions": [{"ref_id":"...","reason":"...","recoverable":true,"action":"..."}],
  "status":"ok", "uncertainty":[], "pending_human":[], "limitations":[], "note":""
}
```

## 6. 边界

- 不替代数据库接入、全文阅读或系统综述的正式纳排流程。
- 无参考集时 `coverage.denominator=0` 且 `status=insufficient_evidence`，不编造分母。
- 冲突信息（同 DOI 标题不一致）并存并标 `title_conflict_same_doi`，不抹平。
- 撤稿/无法获取全文/标识冲突在 `identifier_issues` 或 `status_report.conflicts` 中标注，不猜归属。
