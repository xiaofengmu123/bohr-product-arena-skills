# result.json 输出契约与字段规范

本文档说明 `/app/output/result.json` 的字段语义与核验标准，供 Agent 与人工评审复核。所有字段由 `scripts/main.py assemble` 自动生成；Agent 也可先手工编辑 `/app/work/` 下的中间状态再重新 `assemble`。

## 1. 顶层字段（`*` 为强制）

| 字段 | 必需 | 说明 |
|-|-|-|
| `case_id`* | 是 | 取自 `case.json` 的 `case_id/caseId/id/challenge_id/armiId`；缺失时按文件名派生，恒非空 |
| `status`* | 是 | `ok \| partial \| insufficient_evidence \| input_error` |
| `research_question` | 否 | 研究问题（若无则 `null`） |
| `search_scope` | 否 | 输入文件、契约键、检索规则 |
| `results`* | 是 | 候选文献列表，按 `score` 降序、`year` 降序、标题升序确定排序 |
| `evidence`* | 是 | 查询/筛选/排序/追踪证据记录 |
| `sources`* | 是 | 去重后的来源清单（database/doi/url） |
| `uncertainty`* | 是 | `missing_metadata` / `conflicts` / `coverage_gaps` / `notes` |
| `status_notes` | 否 | 空结果、冲突、降级等状态说明 |
| `limitations` | 否 | 缺失与局限，如实标注 |
| `pending_human` | 否 | 需人工复核的条目 |
| `errors` / `degradations` | 否 | 输入解析失败与降级记录 |
| `change_log`* | 是 | `{time, action, reason, status}` 时间有序 trace |
| `reproducibility`* | 是 | `fingerprint`：剔除时间戳后的主内容确定性指纹 |
| `runtime` | 否 | 本次运行的 work_dir 与说明（时间戳仅作 trace） |

## 2. 每条 `results[*]` 的字段

- `rank`：1 起排序名次；
- `title` / `authors` / `year` / `journal` / `doi` / `url` / `database`：元数据，缺失填 `null` 或 `[]`；
- `relevance`：0–1 相关性分（由 Agent 依据材料判断）；
- `relevance_reason`：相关性依据（一句话，说明为什么相关）；
- `score`：`0.6·relevance + 0.2·metadata_completeness + 0.2·source_reliability`；
- `score_components`：上述三项拆分的原始值；
- `evidence`：证据数组，每条 `{locator, quote, kind}`，`quote` 必须是来源逐字片段；
- `verified`：布尔值；无逐字证据（quote 为空）时自动置 `false`，不得冒充已核验；
- `fingerprint`：去重键（`d:<doi>` 或 `t:<norm_title>[@year]`）。

## 3. 证据溯源硬要求

- 每个关键结论/每条结果都必须有 `locator` + 逐字 `quote`；
- `locator` 形式建议：`paper_search:crossref`、`web_search:<query>`、`materials/xxx.pdf#p3`；
- 冲突不隐藏：同一指标多来源不一致时全部保留并写 `uncertainty.conflicts`，标 `source_conflict`；
- 不虚构 DOI/数值/引用；缺失写 `pending_human` 或 `null`，绝不示例填充。

## 4. output_contract 回填规则

- 题目 `output_contract`（或 `output/schema/required_fields` 等）的全部键必须**原样**出现在 result.json 顶层；
- 值回填为 `null` / `[]`，不改名、不删未知键、不改变键结构；
- 契约中 `fields` / `required_fields` / `properties` 命名的键若不在 result 中，补齐为 `null`。

## 5. status 判定

- 有候选且无输入降级 → `ok`；
- 有候选但存在输入降级 → `partial`；
- 无候选 → `insufficient_evidence`；
- 既无 `case.json` 也无 `task.md` → `input_error`。
