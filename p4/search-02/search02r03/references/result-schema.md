# result.json 字段规范（确定性版）

本文件定义 `/app/output/result.json` 的规范与各字段含义，供人工复核与二次开发。脚本按此规范确定性组装；所有顺序固定，键按字典序序列化。

## 1. 顶层字段

| 字段 | 必需 | 说明 |
|-|-|-|
| `case_id` | 是 | 取自 `/input/case.json`（依次尝试 `case_id`/`id`/`challenge_id`/`task_id`），缺失时回退输入目录名 |
| `status` | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error`；由覆盖度与停止判断推导，也可经 `set-plan --kind status` 显式覆盖 |
| `research_question` | 否 | 研究问题原文（优先取输入，缺失为 `null`） |
| `sub_questions` | 是 | 结构化子问题清单（数组） |
| `query_plan` | 是 | 查询规划（数组） |
| `term_expansion` | 是 | 术语扩展（对象） |
| `stop_decision` | 是 | 停止判断（对象） |
| `results` | 是 | 纳入候选（数组） |
| `screening` | 是 | 全量筛选账本（对象） |
| `evidence` | 是 | 查询/筛选/冲突/缺口日志（数组） |
| `sources` | 是 | 来源汇总（数组） |
| `uncertainty` | 是 | 不确定性（对象） |
| `change_log` | 是 | 变更记录（数组） |
| `status_notes` | 否 | 状态说明（数组） |
| `limitations` | 否 | 局限（数组） |
| `errors` | 否 | 解析/接口错误（数组） |
| `degradations` | 否 | 降级记录（数组） |
| `pending_human` | 否 | 需人工补充的缺口（数组） |
| `run_meta` | 是 | 运行时元数据（时间戳、内容哈希），见 §5 |
| `output_contract` 各字段 | 视输入 | 输入契约的全部字段**原样回填**，未获得的为 `null` 或 `[]` |

## 2. 子问题（sub_questions）

每个子问题尽量标注六要素，至少含稳定 `id`：

```json
{
  "id": "SQ1",
  "object": "研究对象",
  "mechanism": "机制",
  "method": "方法",
  "time": "时间范围",
  "comparison": "比较关系",
  "evidence_requirement": "证据要求"
}
```

`query_plan` 条目建议含 `sub_question_ids`、`query`、`depends_on`（前置依赖，空为可并行）、`order`、`rationale`。

## 3. 候选记录（results 与 screening）

每条 `results` 条目字段：

| 字段 | 说明 |
|-|-|
| `rank` | 排序位次（缺省按导入顺序 + 稳定排序补位） |
| `title` / `authors` / `year` / `journal` / `doi` / `url` / `database` | 元数据，逐字保留来源写法；缺失填 `null` |
| `sub_question_ids` | 命中的子问题 id 列表 |
| `relevance` | 相关性 0–1 |
| `relevance_reason` | 相关性理由（须具体） |
| `screening` | `included` / `excluded` / `uncertain` / `merged_duplicate` |
| `screening_reason` | 筛选理由（排除/待定必须给） |
| `evidence` | 证据数组，每条 `{"locator": "...", "quote": "逐字引用"}` |
| `score` | 脚本计算的总分；若模型提供四分量则由脚本复算 |
| `score_breakdown` | 四分量对象 `{"relevance", "evidence", "metadata", "reliability"}`（各 0–1） |

`screening` 对象结构：

```json
{
  "counts": {"included": 0, "excluded": 0, "uncertain": 0, "merged_duplicates": 0, "total": 0},
  "ledger": [{"title": "...", "year": 0, "screening": "excluded", "screening_reason": "...", "sub_question_ids": []}],
  "merged_duplicates": [{"kept": "...", "dropped": "...", "reason": "duplicate of already-imported record"}]
}
```

排除项**不静默丢弃**：全部出现在 `ledger` 并带理由。

## 4. 打分公式（脚本固化，可复现）

```
score = 0.55 · relevance + 0.20 · evidence + 0.15 · metadata + 0.10 · reliability
```

- `relevance`：与子问题直接相关程度（0–1）。
- `evidence`：是否可指向材料中的逐字片段（有 locator+quote 记为 1，仅有摘要记为 0.5，无记为 0）。
- `metadata`：`doi/journal/year/authors` 完整度（每缺一项扣 0.25）。
- `reliability`：来源可信度（正式期刊/论文全文 > 预印本 > 网络摘要）。

脚本在 `assemble` 时按该公式复算 `score`（四舍五入到 3 位小数）；若分量缺失，`score` 为 `null`。

## 5. run_meta 与确定性

`run_meta` 只放运行时信息，**不进入分析正文**：

```json
{
  "schema_version": 1,
  "started_at": "...",
  "finished_at": "...",
  "duration_seconds": null,
  "model_requests_used": null,
  "input_sha256": "...",
  "state_sha256": "...",
  "deterministic_content_sha256": "..."
}
```

`deterministic_content_sha256` = 对 result.json 去掉 `run_meta` 后按 `sort_keys` 序列化的 SHA-256。同一输入两次运行，该值与分析正文应字节一致；`started_at/finished_at/duration_seconds` 允许随运行变化。`manifest.json` 同步保存三个哈希，便于独立复核。
