# result.json 字段规范（与 scripts/core.py 保持一致）

本文件是 `scripts/core.py` 生成 `result.json` 的权威字段说明。任何字段的语义以此为准；脚本是确定性实现，不做推理。

## 顶层字段

| 字段 | 类型 | 含义 |
|-|-|-|
| `case_id` | string | 来自输入 `case.json`（`case_id`/`id`/`caseId`/`challenge_id` 任一）；缺失为 `null` |
| `status` | string | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，诚实反映完成度 |
| `research_question` | string | 案例研究问题原文（从 case 提取或由 set-plan 写入） |
| `sub_questions` | array | 拆解出的子问题，每条见下 |
| `query_plan` | array | 查询顺序与依赖规划 |
| `term_expansion` | array | 同义/缩写/跨语言/上下位术语扩展 |
| `stop_decision` | object | 停止判断：`status`(continue/stop/handoff)、`reason`、边际收益等 |
| `results` | array | included 候选（按 score 降序，带 `rank`），见下 |
| `screening` | object | 全量筛选账本：`total_records` + `included`/`excluded`/`uncertain`/`merged_duplicates` |
| `evidence` | array | 每轮查询/筛选/排序/停止依据日志 |
| `sources` | array | 数据源清单 |
| `uncertainty` | object | `missing_metadata`/`conflicts`/`coverage_gaps`/`notes` |
| `change_log` | array | 增量变更：`time`/`action`/`reason`/`status` |
| `status_notes` | string | 状态说明 |
| `limitations` | array | 明确限制 |
| `errors` | array | 已捕获错误（坏 JSON、目录缺失等） |
| `degradations` | array | 非致命降级（超限跳过、编码回退、输出目录回退等） |
| `pending_human` | array | 需人工补充的缺口 |
| `runtime` | object | `started_at`/`finished_at`/`seconds`/`requests` |

此外，`case.json` 中的 `output_contract`（或 `output`/`schema`/`required_fields`/`contract`）的**全部字段原样回填到顶层**：未获得时按规范填 `null`/`[]`/`{}`，不改名、不删未知键。

## sub_question 字段

```json
{
  "id": "SQ1",
  "text": "可检索的子问题陈述",
  "object": "研究对象",
  "mechanism": "机制",
  "method": "方法",
  "time_range": "时间范围",
  "comparison": "比较关系",
  "evidence_requirement": "证据要求"
}
```

六要素（对象/机制/方法/时间范围/比较/证据要求）是本 Skill 拆解的最小标注集；缺项填 `null`。

## result（included 候选）字段

| 字段 | 含义 |
|-|-|
| `rank` / `id` / `title` / `authors` / `year` / `journal` / `doi` / `url` / `database` | 排序位次与元数据；元数据逐字保留来源写法，缺失为 `null` |
| `sub_question_ids` | 对应子问题 |
| `relevance` / `relevance_reason` | 相关性评分（0..1）与依据 |
| `screening` / `screening_reason` | 筛选决定与理由（included/excluded/uncertain/merged_duplicate） |
| `evidence` | `{locator, quote}`：locator 为材料定位，quote 为逐字原文 |
| `score` / `score_breakdown` | 透明打分与分解 |

## 打分公式（脚本固化、可复现）

```
score = 0.6 * relevance
      + 0.2 * metadata_completeness   # authors/year/journal/doi 四者非空比例
      + 0.2 * source_reliability      # 见 core.SOURCE_RELIABILITY 映射
```

`relevance` 由 Agent 给出；`metadata_completeness` 与 `source_reliability` 由脚本确定性计算。任何来源缺 `database` 时按 0.5 计可靠度；`materials/` 本地 PDF 计 0.8。

## 证据记录 evidence 字段

```json
{
  "query": "检索式",
  "source": "paper_search | web_search | materials/pdf",
  "database": "来源库",
  "timestamp": "ISO 时间",
  "rationale": "为什么这轮检索",
  "kind": "query | screening | ranking | stop"
}
```

冲突证据不隐藏：同一指标两来源不一致时，两处都记录并标 `source_conflict`（写入 `uncertainty.conflicts`）。
