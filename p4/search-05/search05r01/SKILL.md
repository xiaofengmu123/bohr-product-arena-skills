---
name: multi-db-federated-search
description: 多学术数据库联合检索的科研检索 Skill（证据溯源强化版）：发现并解析 /input 下 Crossref、EuropePMC、PubMed、出版社等异构响应与论文，完成跨库字段归一、可解释去重、优先级合并、来源回溯与覆盖缺口比较，为每条关键结论保留 locator + 逐字 quote，交付可逐条回源复核的 result.json。当需要围绕一个科研问题跨多个文献库检索、去重、合并并给出候选集与遗漏说明时使用。
version: 2.0.0
---

# 多数据库联合检索 Skill（证据溯源强化）

## 1. 角色与何时使用

把「多库检索」沉淀为确定、可复核、可重复的工作流。适用：给定科研问题，从 `/input` 下多个学术数据库响应（Crossref / EuropePMC / PubMed / 出版社/聚合器）与论文 PDF、笔记中抽取文献，统一字段、去重合并、保留来源、报告覆盖缺口。不适用：绕过服务限制、单库全文精读、把未授权数据汇总到未批准服务、需要联网实时检索（本 Skill 不联网，只处理 `/input` 已有材料）。

核心立场：不看接入了几个库、发了多少请求，只看三点——不同库结果能否归并成**不重复、字段可用**的记录集；每条记录关键字段能否**逐条回溯**到来源库与原始响应（locator + 逐字 quote）；哪些库/哪些字段没覆盖能否**明确说出**而不是静默吞掉。

## 2. 输入契约

运行时固定输入：

- `/input/case.json`：案例元数据。可能含 `case_id`、`research_question`/`question`/`task`、`queries`/`keywords`/`search_terms`、`databases`/`search_scope`，以及可选的 `output_contract`（若存在，其全部键必须**原样回填**到 result.json：缺值填 `null`/`[]`，不改名、不删未知键）。
- `/input/task.md`：任务说明（事实依据，不是答案来源）。
- `/input/` 其他材料：数据库响应 `*.json`、论文 `*.pdf`、`*.txt`/`*.md` 笔记。

先发现、再假设：不假定固定文件名/键名，扫描 `/input/` 全部文件并按结构识别类型。文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级不崩溃）；单文件 >2MB 跳过并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，结构 = `case_id` + 题目 `output_contract` 全部字段 + 固定字段：

```json
{
  "case_id": "string",
  "status": "ok|partial|insufficient_evidence|input_error",
  "research_question": "string|null",
  "search_scope": {"databases": [], "queries": [], "derived_query_terms": []},
  "candidates": [ {"record_id","title","authors","year","journal","volume","issue","pages","doi","pmid","pmcid","issn","doc_type","abstract","publisher","identifiers","provenance","field_sources","relevance_hint"} ],
  "ranking": [ {"record_id","rank","relevance","rationale"} ],
  "evidence_records": [ {"evidence_id","type","db","source_file","locator","quote"} ],
  "evidence_map": {"claims":[], "field_sources":{}, "dedup_clusters":[], "conflicts":[], "coverage_evidence":[], "reference_links":[]},
  "coverage": {"databases":[], "total_input_records", "merged_record_count", "duplicates_merged", "records_in_single_db_only", "systematically_missing_fields", "per_db_fields_missing"},
  "empty_results": [], "conflicts": [], "missing_metadata": [], "permission_issues": [], "limitations": [],
  "sources": [], "uncertainty": {"items": [], "note": ""},
  "change_log": [], "errors": [], "degradations": [], "pending_human": [], "runtime": {}
}
```

- `status` 诚实取值：输入不可用→`input_error`；无候选→`insufficient_evidence`；有缺口/冲突/降级→`partial`；否则 `ok`。
- `evidence_map` 五面 + `reference_links` 是本版强化点，语义见 `references/evidence-spec.md`。
- `change_log` 首条必须是「先落盘骨架」；此后只做增量改进。

辅助产物（可打开、可编辑、可复现）：`merged_records.json`（全量合并记录）、`evidence_map.json`（证据五面）、`coverage_report.md`（人读覆盖报告）。

## 4. 执行流程（总预算：900 秒、64 次模型请求，每响应 ≤16384 tokens）

**阶段 0（第 0–3 分钟，先落盘骨架）**：读 `/input/case.json` 与 `/input/task.md`，立即执行一次管线，脚本首步就写 `status=partial` 的合法骨架：

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
```

（只想先落骨架再思考：`python3 -B scripts/main.py --skeleton-only --input /input --output /app/output`。）此阶段结束前必须确认 `/app/output/result.json` 存在且为合法 JSON、`status=partial`。此后只做增量改进，不重头开始。

**阶段 1（第 3–10 分钟，材料解析与融合）**：检查 `coverage_report.md` 与 `merged_records.json`；对照 `references/field-mapping.md` 核对字段映射、去重簇、合并优先级；发现映射错误或漏并时修脚本，重新运行主脚本。

**阶段 2（第 10–13 分钟，相关性筛选与排序）**：脚本已给出确定性 `relevance_hint`（词项命中）与缺省排序；模型读候选标题/摘要，按研究问题判相关性，把人工排序、筛选、状态、局限写入 `/app/work/decisions.json`：

```json
{ "status": "partial",
  "ranking": [ {"record_id": "rec:1", "rank": 1, "relevance": "high", "rationale": "..."} ],
  "uncertainty": {"items": [], "note": ""},
  "limitations": ["..."], "pending_human": ["..."] }
```

然后重跑 `python3 -B scripts/main.py --decisions /app/work/decisions.json --input /input --output /app/output --work /app/work`。

**阶段 3（第 13–15 分钟，自检与收尾）**：执行自检，逐项勾选，修复 FAIL 后重跑：

```bash
python3 -B scripts/selftest.py --input /input --output /app/output
```

## 5. 脚本说明（相对路径调用）

- `scripts/main.py`：流水线入口。缺省 `--input /input --output /app/output --work /app/work`；`--skeleton-only` 只写骨架；`--decisions` 合并模型/人工仲裁。
- `scripts/core_impl.py`：核心库（被 main/selftest 导入），解析、归一、去重、合并、覆盖、证据。
- `scripts/selftest.py`：交付前自检，退出码 0=全通过；`--repro` 附加同输入两次运行的确定性比对。
- 规则文档：`references/field-mapping.md`（映射/去重/合并）、`references/evidence-spec.md`（证据溯源规范）。

## 6. 自检清单（对应评分维度）

- [ ] `result.json` 合法、含 `case_id`、`status` 合法；output_contract 字段无缺键
- [ ] 每条候选有 `provenance`（库—查询—原始响应—映射—去重—合并可回溯）与 `raw_snapshot`
- [ ] 每条 `field_value` 证据有 `locator` + 逐字 `quote`，且可按 pointer+key_path 回源核对
- [ ] 去重/合并/冲突消解可解释；`evidence_map.dedup_clusters/conflicts` 结构齐全
- [ ] `coverage` 列明每库 `per_db_fields_missing` 与 `records_in_single_db_only`；缺席写入 `coverage_evidence`
- [ ] 同输入重复运行结果确定；空输入/坏 JSON/缺软件均不崩溃
- [ ] 无伪造：缺失填 `null`，缺口写 `limitations`/`degradations`，不虚构 DOI/PMID/引文/年份/命中数

## 7. 降级策略与边界

缺数据/缺软件必须如实标注，禁止编造：某库响应缺失→该库不进 `sources`/`coverage`，`limitations` 注明未覆盖；字段缺失→`null` + `missing_metadata` + `coverage_evidence`；冲突→`conflicts` 并标 `needs_human_arbitration`；文件 >2MB→跳过并记 `degradations`；缺 `pdftotext`→PDF 仅列路径；无候选→`insufficient_evidence` 并列缺口。不虚构评测分数、不以示例填充充当正式结果。

最小调用示例（示意，标明为示例）：

```bash
python3 -B scripts/main.py --input ./demo/input --output ./demo/output
python3 -B scripts/selftest.py --input ./demo/input --output ./demo/output
# 示例输出：{"case_id":"demo","status":"insufficient_evidence","result":".../result.json"}
```
