# result.json 字段规范（可复核契约）

本文件定义 `/app/output/result.json` 的字段语义，供 Agent 执行与评审复核；
与 `scripts/main.py` 的 `assemble` 实现保持一致。所有字段均为确定性输出：
不含墙钟时间，时间顺序用 `change_log[].seq` 单调递增表达。

## 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id`* | string | 取自输入的案例标识（多重回退，未知时为 `unknown`） |
| `status`* | enum | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `research_question` | string\|null | 解析出的研究问题，未解析到为 `null` |
| `search_scope` | string\|null | 检索范围（主题/时间/语言/库），由 Agent 在运行时补充 |
| `results`* | list | 候选文献列表，每条字段见下 |
| `evidence`* | list | 查询/筛选/排序/追踪证据，每条字段见下 |
| `sources`* | list | 唯一来源汇总（database/source/type/reliability） |
| `uncertainty`* | object | `missing_metadata`/`conflicts`/`coverage_gaps`/`notes` |
| `pending_human`* | list | 需人工核验的项（缺 DOI/URL、缺逐字 quote 等） |
| `change_log`* | list | `{seq, action, reason, status}`，确定性变更记录 |
| `status_notes`* | list | 空结果、冲突、权限、接口异常等状态说明 |
| `limitations` | list | 本结果的局限（诚实标注，不补造） |
| `errors` | list | 运行错误 |
| `degradations` | list | 输入/编码/解析降级项 |
| `runtime` | object | `{budget_seconds:900, model_requests_budget:64, deterministic:true}` |
| `determinism` | object | `{fingerprint, excluded_fields, repeat_check}` |
| `outputs` | object | 产物相对路径 `{result, candidates, plan}` |
| `contract` | object | 题目 `output_contract` 原始字段（原样回填） |
| `<contract 字段>` | any | 非标准冲突键扁平回填到顶层（值为 `null`/`[]` 或原值） |

`*` 为强制字段；`output_contract` 的全部字段必须出现在 `contract` 中，其中不
与标准字段重名的键同时扁平回填到顶层，改名/删键均不允许。

## 候选记录（results[]）

- `rank` int：排序名次（1 起）
- `title` string：真实标题（必填，否则缺省）
- `authors` list：作者字符串列表
- `year` int\|null：解析出的四位年份
- `journal` string\|null
- `doi` string\|null：规范化 DOI（小写、去 `doi.org/` 前缀）
- `url` string\|null
- `database` string：来源库（crossref/europepmc/pubmed/arxiv/openalex/web_search/…）
- `relevance` number：Agent 给出的相关性 0..1，缺失按 0.5
- `relevance_reason` string：相关性依据（人话）
- `score` number：`0.6·relevance + 0.2·metadata + 0.2·source_reliability`
- `evidence` object：`{locator, quote}`，逐字证据溯源

去重：DOI 优先，缺失按规范化标题；同键合并时非空字段不回填，作者取更完整一方。

## 证据记录（evidence[]）

- `seq` int：单调序号（确定性顺序）
- `query` string：实际执行的查询
- `source` string：调用工具（paper_search / web_search / materials）
- `database` string：目标库
- `rationale` string：为何执行该查询
- `kind` string：query / filter / rank / trace
- `scope` string\|null：覆盖范围说明

## 确定性设计（本变体侧重点）

1. 全链路无随机数、无墙钟时间；顺序由 `seq` 表达。
2. 排序并列项用「规范化标题升序 + 原始导入顺序」二次排序，杜绝哈希遍历抖动。
3. 指纹 = 对确定性字段做 `json.dumps(sort_keys=True)` 后取 sha256；同输入两次运行
   应逐字节一致。复核命令：`python3 scripts/selftest.py`。
