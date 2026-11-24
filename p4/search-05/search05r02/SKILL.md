---
name: multi-db-federated-search
description: 多学术数据库联合检索的科研检索 Skill：在 900 秒、64 次模型请求预算内，发现并解析 /input 下 Crossref、EuropePMC、PubMed、出版社等异构响应与论文，完成跨库字段归一、确定性去重、记录合并、来源回溯与覆盖比较，前 3 分钟先落盘 partial 结果骨架、之后只做增量改进，产出可逐条复核的 result.json。适用于围绕一个科研问题跨多个文献库检索、去重、合并并交付可回溯候选集与遗漏说明的场景。
version: 1.1.0
---

# 多数据库联合检索 Skill（时间预算·早落盘版）

## 1. 角色与何时使用

本 Skill 把「多库检索」沉淀为确定、可复核、可重复运行的工作流。适用：给定
科研问题，从多个学术数据库响应（Crossref / EuropePMC / PubMed / 出版社）中
抽取文献、统一字段、去重合并、保留来源、报告覆盖缺口。不适用：绕过服务
限制、单库全文精读、把未授权数据汇总到未批准服务、需要联网实时检索的场景
（本 Skill 不联网，只处理 `/input` 里已有的实际响应与材料）。

核心立场只看三点：不同库结果能否归并成**不重复、字段可用**的记录集；每条
记录的关键字段能否**逐条回溯**到来源库与原始响应；哪些库/字段没覆盖能否
**明确说出**而不是静默吞掉。本版强化「时间预算与早落盘」：把 900s / 64
请求拆成阶段预算，前 3 分钟必须先落盘一版 partial 骨架。

## 2. 输入契约

运行时固定输入：

- `/input/case.json`：案例元数据。可能含 `case_id`、`research_question`/
  `question`/`task`、`queries`/`keywords`/`search_terms`、`databases`，以及
  可选的 `output_contract`（若存在，其全部键必须**原样回填**，缺值填
  `null`/`[]`，不改名不删键）。
- `/input/task.md`：任务说明（事实依据，不是答案来源）。
- `/input/` 下的材料：数据库响应 `*.json`（Crossref `message.items[]`、
  EuropePMC `resultList.result[]`、PubMed esummary/esearch、出版社异构
  JSON）、论文 `*.pdf`、`*.txt`/`*.md` 笔记。
- 工作目录 `/app/work/`，最终产物写 `/app/output/`。

先发现、再假设：不假定固定文件名/键名；扫描 `/input/` 全部文件，按扩展名
与结构识别类型。文本按 `utf-8-sig` 读取并容错；单文件 >2MB 跳过并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，结构 = `case_id` + 题目 output_contract
全部字段 + 固定字段：

```json
{
  "case_id": "string",
  "research_question": "string|null",
  "search_scope": {"databases": [], "queries": [], "derived_query_terms": []},
  "candidates": [ { "record_id", "title", "authors", "year", "journal",
    "volume", "issue", "pages", "doi", "pmid", "pmcid", "issn", "doc_type",
    "abstract", "publisher", "identifiers", "field_sources", "provenance",
    "member_records", "dedup_reason", "relevance_hint" } ],
  "ranking": [ { "record_id", "rank", "relevance", "rationale" } ],
  "evidence_records": [ { "evidence_id", "type", "db", "source_file",
    "locator", "quote" } ],
  "coverage": { "databases": [], "total_input_records",
    "merged_record_count", "duplicates_merged", "records_in_single_db_only",
    "systematically_missing_fields", "per_database" },
  "empty_results": [], "conflicts": [], "missing_metadata": [],
  "permission_issues": [], "limitations": [],
  "status": "ok|partial|insufficient_evidence|input_error",
  "sources": [], "uncertainty": { "items": [], "note": "" },
  "change_log": [], "errors": [], "degradations": [],
  "pending_human": [], "runtime": {}
}
```

固定字段含义：`status` 诚实取值（输入不可用→`input_error`；无候选→
`insufficient_evidence`；有缺口/冲突/降级→`partial`；否则 `ok`）；
`sources` 是每个输入文件的类型、路径、记录数；`uncertainty` 逐项不确定性
与总体说明；`change_log` 按时间顺序记录步骤，首条必须是「先落盘骨架」；
`runtime` 记录 `started_at`、`finished_at`、`seconds`、`phases`（每阶段
预算与耗时）、`budget`（900s/64 请求/16384 tokens）。

辅助产物（可打开、可编辑、可复现）：`merged_records.json`（全量合并记录）、
`evidence_map.json`（证据/冲突/缺失）、`coverage_report.md`（人读覆盖报告）。
不产出伪造结果、不冒充正式案例。

## 4. 执行流程与时间预算（总 900 秒、64 次模型请求，每响应 ≤16384 tokens）

**阶段 P0（0–3 分钟，先落盘骨架）**：读 `/input/case.json` 与
`/input/task.md`，立即执行一次管线（脚本首步就写骨架）：

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
```

（若只想先落骨架再思考，先执行 `python3 -B scripts/main.py --skeleton-only
--input /input --output /app/output`。）此阶段结束前必须确认
`/app/output/result.json` 存在且为合法 JSON、`status=partial`。此后只做
增量改进。

**阶段 P1（3–10 分钟，材料解析与融合）**：检查 `coverage_report.md` 与
`merged_records.json`；对照 `references/field-mapping.md` 核对字段映射、
去重簇、合并优先级是否合理；发现映射错误或漏并时修 `core.py`，重跑主脚本。

**阶段 P2（10–13 分钟，相关性筛选与排序）**：脚本已给出确定性
`relevance_hint`（词项命中）与自动排序；模型读候选标题/摘要，按研究问题
判相关性，把人工排序、筛选、状态、局限写入 `/app/work/decisions.json`：

```json
{ "status": "partial",
  "ranking": [ { "record_id": "rec:0001", "rank": 1, "relevance": "high",
    "rationale": "..." } ],
  "uncertainty": { "items": [], "note": "" },
  "limitations": ["..."], "pending_human": ["..."] }
```

然后重跑 `python3 -B scripts/main.py --decisions /app/work/decisions.json
--input /input --output /app/output --work /app/work`。

**阶段 P3（13–15 分钟，自检与收尾）**：执行自检，逐项勾选，修复 FAIL 后
重跑：

```bash
python3 -B scripts/selftest.py --input /input --output /app/output
```

增量改进：`--skeleton-only` 先落骨架；`--resume` 复用
`/app/work/pipeline_state.json` 跳过已完成的解析融合。

## 5. 脚本说明（相对路径调用）

- `scripts/main.py`：流水线入口。参数缺省 `--input /input --output
  /app/output --work /app/work`；`--skeleton-only` 只写骨架；`--decisions`
  合并人工/模型仲裁；`--resume` 增量复用。
- `scripts/core.py`：核心库（被 main/selftest 导入），负责解析、归一、去重、
  合并、覆盖、证据、阶段预算。
- `scripts/selftest.py`：交付前自检，退出码 0=全通过。
- 规则文档：`references/field-mapping.md`（映射与合并规则）、
  `references/checklist.md`（检查清单与降级）。

## 6. 自检清单（对应评分维度）

- [ ] `result.json` 合法、含 `case_id`、`status` 合法；output_contract 字段无缺键
- [ ] 每条候选有 `provenance` 与 `field_sources`（库—查询—原始响应—映射—
  去重—合并可回溯），`member_records[].raw_snapshot` 为逐字片段
- [ ] 去重/合并/冲突消解可解释（`dedup_reason` 非空），`coverage` 列明每库
  `fields_missing` 与仅单库记录
- [ ] `evidence_records` 非空（有候选时），source_file、locator、quote 齐全
- [ ] 同输入重复运行结果确定（去时间戳逐字一致）；空输入/坏 JSON/缺软件不崩溃
- [ ] 无伪造：缺失填 `null`/`not_reported`，缺口写 `limitations`/`degradations`
- [ ] `runtime.phases` 记录阶段预算与耗时，P0 骨架先落盘

## 7. 降级策略与边界

缺数据/缺软件必须如实标注，禁止编造：某库响应缺失→该库不进
`sources`/`coverage`，`limitations` 注明未覆盖；字段缺失→`null`+
`missing_metadata`；冲突→`conflicts`（全量取值+来源）；文件超限→跳过并记
`degradations`；缺 `pdftotext`→PDF 仅列路径；无候选→
`insufficient_evidence` 并列缺口。不虚构 DOI/PMID/引文/年份/命中数/评测
分数；不以示例填充充当正式结果。

## 8. 最小调用示例（示例，非正式案例）

```bash
python3 -B scripts/main.py --input ./demo/input --output ./demo/output
python3 -B scripts/selftest.py --input ./demo/input --output ./demo/output
# 示意输出（示例）：{"case_id":"demo","status":"insufficient_evidence"}
```
