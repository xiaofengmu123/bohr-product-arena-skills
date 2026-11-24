# result.json 字段规范与状态文件约定

本文件是脚本与 Agent 之间的契约。所有字段名对脚本和复核人都可见；缺值用 `null` 或 `[]`，不删除未知键。

## 1. 工作状态文件（/app/work）

| 文件 | 内容 | 写入命令 |
|-|-|-|
| `case_summary.json` | 输入盘点：case_id、research_question、task_text（截断）、output_contract 回显、materials 清单、input_errors、degradations | `run` |
| `plan.json` | 拆解与规划：research_question、key_concepts、sub_questions、query_plan、term_expansion、stop_decision | `run` 初始化，`set-plan` 增量合并 |
| `records.json` | 全部候选记录（含 screening 决策），是所有筛选的账本来源 | `import` 增量合并 |
| `evidence.json` | 证据日志（query / round / conflict / gap） | `evidence` 追加 |
| `meta.json` | status、status_notes、limitations、errors、degradations、pending_human、runtime、change_log | `run` 初始化，`set-meta` 定稿 |

`run` 只初始化状态文件；后续一律用 `set-plan` / `import` / `evidence` / `set-meta` 增量更新，禁止重跑 `run` 覆盖计划。

## 2. 主产物 /app/output/result.json

强制字段（`*` 为强制）：

- `case_id`*：取自输入；取不到时用输入目录名兜底，不得为空。
- `status`*：`ok | partial | insufficient_evidence | input_error`。
- `research_question`、`sub_questions`*、`query_plan`*、`term_expansion`*、`stop_decision`*。
- `results`*：纳入候选；每条含 `rank / record_id / title / authors / year / journal / doi / url / database / sub_question_ids / relevance / relevance_reason / screening / screening_reason / evidence / score / score_breakdown`。
- `screening`*：全量筛选账本，含 `included / excluded / uncertain / merged_duplicates / unscreened / total`。排除、待定、合并重复都必须有理由；`unscreened` 必须为空才算完成全量筛选。
- `evidence`*：逐条 `kind / source / database / timestamp / query / rationale / result_count`。
- `sources`*：`databases / materials / searches`。
- `uncertainty`*：`missing_metadata / conflicts / coverage_gaps / notes`。
- `change_log`*：`time / action / reason / status`。
- `status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`。
- 题目 `output_contract` 的全部字段：原样回填，未获得的填 `null` 或 `[]`。

## 3. 全量筛选决策语义

| screening 值 | 含义 | 必需字段 |
|-|-|-|
| `included` | 纳入候选集 | relevance、relevance_reason、evidence（每条含 locator + quote） |
| `excluded` | 排除，但不静默丢弃 | screening_reason |
| `uncertain` | 证据不足待定 | screening_reason |
| `merged_duplicate` | 重复导出，合并入另一条 | merged_into（指向保留记录）+ screening_reason |

原则：**每一条返回记录都必须有且仅有一个决策**；同一 DOI/标题的重复导出用 `merged_duplicate` 归并并保留来源；互相冲突的两条记录各自保留并标 `conflict=true`，写进 `uncertainty.conflicts`，不允许擅自二选一。

## 4. 证据溯源

每条 `included` 结果必须给出 `evidence` 数组，每条至少含：

- `locator`：可定位到材料的路径/页码/段落，如 `materials/paper-a.pdf#p3`。
- `quote`：材料中的逐字原文（短句，不转述、不脑补）。

脚本在 `selfcheck` 中强制检查纳入结果的 `locator + quote` 是否齐备。

## 5. 打分公式（透明、可复现）

```
score = 0.6 * relevance + 0.2 * metadata_completeness + 0.2 * source_reliability
```

- `relevance`：Agent 对该结果与子问题相关性的评分（0–1）。
- `metadata_completeness`：`title/authors/year/journal/doi` 五项中非空的占比。
- `source_reliability`：来源库为期刊/DOI/Crossref/OpenAlex/Scopus/WoS/Semantic Scholar/arXiv 等记 1.0；其他具名库 0.7；无来源 0.5。

`score_breakdown` 保存三项与总分，供人工复核每一项权重是否成立。

## 6. 确定性说明

同一输入与同一状态文件下，`assemble` 的输出确定（`sort_keys=True`、按 record_id 自然序排序）。`runtime.seconds/requests` 与 `change_log[*].time` 反映真实运行，因此“幂等”指语义幂等（研究问题/子问题/结果/筛选账本一致），时间戳与运行时计数按实际记录。
