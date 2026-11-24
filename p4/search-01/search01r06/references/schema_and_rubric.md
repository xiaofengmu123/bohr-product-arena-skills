# 结果 schema 与打分 rubric（可复核）

本文件规定中间产物与最终产物的字段含义，以及透明排序的打分规则。所有规则均由 `scripts/core_impl.py` 确定性实现，可复现。

## 1. 中间产物（`/app/work/`）

| 文件 | 内容 | 作用 |
|-|-|-|
| `case_summary.json` | 解析后的案例概要（case_id、research_question、search_scope、output_contract 模板、material_files） | 输入归一化的唯一事实源 |
| `plan.json` | 结构化查询计划：`query_steps[]`（step/query_id/concept/terms/synonyms/abbreviations/cross_language/operator/targets/purpose/success_criteria/status）与 `term_expansion_checklist` | 让"多轮查询规划 + 关键词扩展"成为可复核的结构化数据，而非自由文本 |
| `records.json` | 去重前的候选记录（upsert 后的当前状态） | 候选召回的唯一事实源 |
| `evidence.json` | 查询/筛选/排序/来源证据（evidence_id/kind/query/source/database/timestamp/rationale/locator/quote/query_id） | 每条结果可指回原始查询 |
| `import_state.json` | 每次导入的增量状态（imported/updated/skipped/total/label/at） | 支持"组织方本地增量导入状态"复核 |
| `rubric.json` | 打分权重与来源可靠度映射 | 打分规则可替换、可审计 |
| `state.json` | 首轮启动时间戳 | 计算 runtime |
| `change_log.json` | 步骤变更记录（time/action/reason/status） | 追踪执行过程 |
| `selfcheck.json` | 自检结果 | 交付前核验 |

`manifest.json`（`/app/output/`）记录上述中间产物与最终 `result.json` 的 SHA-256 与字节数，形成内容指纹链。

## 2. 候选记录 schema（`results[]` 元素）

| 字段 | 说明 |
|-|-|
| `record_id` | 稳定标识：`doi:<规范化DOI>` 或 `title:<规范化标题>+hash8` |
| `title` / `authors` / `year` / `journal` / `doi` / `url` | 元数据；缺失给 `null` 或 `[]` |
| `database` | 来源数据库标签 |
| `relevance` | 相关性 0–100（由执行 Agent 判断） |
| `relevance_reason` | 相关性依据的一句话说明 |
| `score` / `score_breakdown` | 综合分与三因子分解 |
| `evidence` | `[{locator, quote, kind, source}]` 逐字证据 |
| `dedup` | `{canonical, duplicates[], method}` |
| `provenance` | `{tool, retrieved_at, query_id[], note}` |
| `verified_metadata` / `conflicts` | 元数据核验与来源冲突 |

## 3. 打分规则

综合分 = 100 × (0.6·relevance_norm + 0.2·metadata_completeness + 0.2·source_reliability)

- `relevance_norm` = relevance / 100；未标相关性记 0（诚实，不猜测）。
- `metadata_completeness` = 在 `[title, authors, year, journal, doi]` 中非空字段占比。
- `source_reliability` 按数据库标签查 `rubric.json` 映射（crossref 1.0、europepmc 0.95、publisher 0.95、paper_search 0.8、materials 0.7、web_search 0.6、unknown 0.5）。

排序键：`(score 降序, year 降序, 规范化标题升序)`，并列时结果稳定。

## 4. 去重规则

- 优先按规范化 DOI（去 `https://doi.org/` 前缀与尾随标点、转小写）。
- 无 DOI 按规范化标题（去非字母数字、折叠空白）。
- 每组保留元数据更完整且来源可靠度不低于的代表记录，其余记入 `dedup.duplicates`。
- 不删除冲突来源：同一指标多来源不一致时在 `conflicts` 记录，不擅自取舍。

## 5. 证据溯源要求

每个关键结论/数值/图元都要指回材料逐字片段：`locator`（如 `materials/x.pdf#p3`、`case.json#research_question`）+ `quote`（逐字引用）。无逐字来源则 `locator`/`quote` 写 `null`，并将无法判断项列入 `pending_human`，不得编造。

## 6. output_contract 回填

`case.json` 中 `output_contract`/`output`/`schema`/`required_fields` 等键的全部字段必须原样回填进 `result.json`：缺失给 `null` 或 `[]`，不改名、不删未知键。
