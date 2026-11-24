# result.json 结构规范（可复核契约）

本文件是 `scripts/main.py assemble` 输出 `/app/output/result.json` 的字段约定，
供他人复核与二次开发。字段值由工作区中的 `records.json / evidence.json /
change_log.json / budget.json / case_summary.json` 确定性推导，不引入随机性。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `case_id` | string | 是 | 案例标识，取自输入；缺失时退化为 `case:<文件名>` 或 `unknown` |
| `status` | string | 是 | `ok \| partial \| insufficient_evidence \| input_error` |
| `results` | list | 是 | 排序后的候选文献，每项见下 |
| `evidence` | list | 是 | 查询/来源证据日志（query/source/database/timestamp/rationale/kind） |
| `status_notes` | list | 是 | 空结果、接口异常、无研究问题等状态说明 |
| `sources` | list | 是 | 去重后的来源与数据库列表 |
| `uncertainty` | object | 是 | `missing_metadata / conflicts / coverage_gaps / notes` |
| `change_log` | list | 是 | `time / action / reason / status` 审计记录 |
| `research_question` | string | 否 | 从输入解析的研究问题 |
| `search_scope` | string | 否 | 检索范围与概念/查询数量的机器摘要 |
| `limitations / errors / degradations / pending_human` | list | 否 | 限制与异常说明 |
| `runtime` | object | 否 | `started_at / completed_at` |
| `budget` | object | 否 | 900s/64 请求预算与已用请求数 |
| 题目 `output_contract` 字段 | 任意 | 视题目 | 全部原样回填：有真实值用真实值，未获得给 `null` 或 `[]` |

## results[] 单项

| 字段 | 说明 |
|-|-|
| `rank` | 从 1 开始的连续序号 |
| `title` | 标题（规范化去重依据之一） |
| `authors` | 作者列表 |
| `year` | 年份（缺失为 `null`） |
| `journal` | 期刊/会议/发布容器 |
| `doi` | DOI（规范化：小写、去前缀） |
| `url` | 来源链接；缺省时若有 DOI 自动补 `https://doi.org/<doi>` |
| `database` | 来源库（crossref/europepmc/publisher/arxiv/…） |
| `relevance` | 0–1 相关性（未给出时按 0.5 记入并写入 uncertainty.notes） |
| `relevance_reason` | 相关性判定依据 |
| `score` | 综合得分，见 `query_and_dedup.md` 的透明打分公式 |
| `score_breakdown` | `relevance / completeness / provenance` 三部分得分 |
| `evidence` | 逐条证据，每项含 `locator + quote + source + kind`；无 quote 不采信 |

## evidence 证据的硬要求

- 每个关键结论（检索命中、元数据取值、相关性判断）都要能指回逐字片段：
  `locator` 说明定位（如 `crossref.title`、`europepmc.abstract`、`pdf#p3`），
  `quote` 为原文逐字引用（上限 2000 字符）。
- 冲突信息不隐藏：同一指标两个来源不一致时，两条都记录并在
  `uncertainty.conflicts` 标注 `source_conflict`。
- 缺失元数据写 `null` 并列入 `uncertainty.missing_metadata`；无法判断的写
  `pending_human`，不猜测、不补造。

## status 判定规则

| 条件 | status |
|-|-|
| 无候选记录 | `insufficient_evidence` |
| 有候选但无证据日志 | `partial` |
| 输入坏 JSON/读取失败且无候选 | `input_error` |
| 有候选且有证据日志 | `ok` |
