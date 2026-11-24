---
name: multi-db-federated-search
description: 多学术数据库联合检索的科研检索 Skill：解析 /input 下 Crossref、EuropePMC、PubMed、出版社等异构响应与论文，完成跨库字段归一、论文身份去重、记录合并、来源回溯与覆盖比较，产出字段逐一对齐 output_contract 且可复核的 result.json。当需要围绕科研问题跨多个文献库检索、去重、合并、并交付可逐条核对候选集与遗漏说明时使用。
version: 2.0.0
---

# 多数据库联合检索 Skill

## 1. 角色与何时使用

本 Skill 把「多库联合检索」沉淀为确定、可复核、可重复的工作流：从多个数据库响应中抽取文献、统一字段、按论文身份去重、合并重复记录、保留来源、报告覆盖缺口。适用：给定科研问题，处理 `/input` 下已有的 Crossref / EuropePMC / PubMed / 出版社异构 JSON 与论文 PDF。不适用：绕过服务限制、单库全文精读、实时联网检索（本 Skill 不联网，只处理 `/input` 已有实际材料）。

核心立场只验三点：不同库结果能否归并成**不重复、字段可用**的记录集；每条记录关键字段能否**逐条回溯**到来源库与原始响应；哪些库/字段没覆盖能否**明确说出**。不以接入库数、请求数论优劣。

## 2. 输入契约

运行时固定输入（先发现、再假设，不假定固定文件名/键名）：

- `/input/case.json`：案例元数据。可含 `case_id`、`research_question`/`question`/`task`、`queries`/`keywords`/`search_terms`、`search_scope`、`databases`，以及可选的 `output_contract`（若存在，其全部键必须**原样回填**，缺值填 `null`/`[]`，不改名、不删未知键；支持 list、`{"fields":[...]}`、`{"field":"type"}` 三种写法）。
- `/input/task.md`：任务说明（事实依据，不是答案来源）。
- `/input/` 其余材料：数据库响应 `*.json`（Crossref `message.items[]`、EuropePMC `resultList.result[]`、PubMed esummary/esearch、出版社异构 JSON）、论文 `*.pdf`、`*.txt`/`*.md` 笔记。
- 工作目录 `/app/work/`，最终产物写 `/app/output/`。

扫描 `/input/` 全部文件，按扩展名与结构识别类型。文本按 `utf-8-sig` 读取并容错；单文件 >2MB 跳过并记 `degradations`。缺 `pdftotext` 时 PDF 仅列路径并降级。

## 3. 输出契约

必须产出 `/app/output/result.json`，结构 = `case_id` + 题目 `output_contract` 的全部字段 + 固定字段：

```json
{
  "case_id": "string",
  "research_question": "string|null",
  "search_scope": {"databases": [], "queries": [], "derived_query_terms": []},
  "candidates": [ { "record_id", "title", "authors", "year", "journal", "volume", "issue", "pages", "doi", "pmid", "pmcid", "issn", "doc_type", "abstract", "publisher", "identifiers", "field_sources", "provenance", "relevance_hint", "raw_snapshot" } ],
  "ranking": [ { "record_id", "rank", "relevance", "rationale" } ],
  "evidence_records": [ { "evidence_id", "type", "db", "source_file", "locator", "quote" } ],
  "coverage": { "databases", "per_database", "total_input_records", "merged_record_count", "duplicates_merged", "records_in_single_db_only", "systematically_missing_fields" },
  "empty_results": [], "conflicts": [], "missing_metadata": [], "permission_issues": [], "limitations": [],
  "status": "ok|partial|insufficient_evidence|input_error",
  "sources": [], "uncertainty": {"items": [], "note": ""},
  "change_log": [], "errors": [], "degradations": [], "pending_human": [], "runtime": {}
}
```

- `status` 诚实取值：输入不可用→`input_error`；无候选→`insufficient_evidence`；有缺口/冲突/降级→`partial`；否则 `ok`。
- `candidates[i].provenance` 记录「库—查询—原始响应—映射—去重—合并」链；`field_sources` 逐字段标注胜出来源；`raw_snapshot` 为原始响应逐字片段。
- `evidence_records` 每条含 `source_file`+`locator`+逐字 `quote`。
- `change_log` 按时间顺序记录步骤，首条必须是「先落盘骨架」。

辅助产物：`merged_records.json`（全量合并记录与去重簇）、`evidence_map.json`（证据/冲突/缺失）、`coverage_report.md`（人读覆盖报告）。不产出伪造结果、不冒充正式案例。

## 4. 执行流程（总预算：900 秒、64 次模型请求，每响应 ≤16384 tokens）

**阶段 0（第 0–3 分钟，先落盘骨架）**：读 `/input/case.json` 与 `/input/task.md`，然后立即执行一次管线（脚本首步就写骨架）：

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
```

（只想先落骨架再思考：`python3 -B scripts/main.py --skeleton-only --input /input --output /app/output`。）此阶段结束前必须确认 `/app/output/result.json` 存在、为合法 JSON 且 `status=partial`。此后只做增量改进。

**阶段 1（第 3–10 分钟，材料解析与融合）**：检查 `coverage_report.md` 与 `merged_records.json`；对照 `references/field-mapping.md` 核对字段映射、去重簇、合并优先级；发现映射错误或漏并时修脚本或改 `decisions.json` 后重跑主脚本。

**阶段 2（第 10–13 分钟，相关性筛选与排序）**：脚本已给出确定性 `relevance_hint`（词项命中）与自动排序；模型读候选标题/摘要，按研究问题判相关性，把人工排序、筛选、状态、局限写入 `/app/work/decisions.json`：

```json
{ "status": "partial",
  "ranking": [ { "record_id": "rec:...", "rank": 1, "relevance": "high", "rationale": "..." } ],
  "uncertainty": { "items": [], "note": "" },
  "limitations": ["..."], "pending_human": ["..."] }
```

然后重跑 `python3 -B scripts/main.py --decisions /app/work/decisions.json --input /input --output /app/output --work /app/work`。

**阶段 3（第 13–15 分钟，自检与收尾）**：执行自检，逐项勾选，修复 FAIL 后重跑：

```bash
python3 -B scripts/selftest.py --input /input --output /app/output
python3 -B scripts/selftest.py --self-test
```

## 5. 脚本说明（相对路径调用，仅标准库）

- `scripts/main.py`：流水线入口。参数缺省 `--input /input --output /app/output --work /app/work`；`--skeleton-only` 只写骨架；`--decisions` 合并人工/模型仲裁。
- `scripts/fedsearch.py`：核心库（被 main/selftest 导入），负责解析、归一、去重、合并、覆盖、证据。
- `scripts/selftest.py`：交付自检（校验 result.json 合法性、契约完整、候选可追溯、证据非空、状态一致性）与异常场景自测（空目录/坏 JSON/缺 case/坏编码/超大文件/重复运行确定性）。
- 规则文档：`references/field-mapping.md`（跨库映射与合并优先级）、`references/checklist.md`（评分维度清单与降级表）。

最小调用示例（示意，标明为示例）：

```bash
python3 -B scripts/main.py --input ./demo/input --output ./demo/output
python3 -B scripts/selftest.py --input ./demo/input --output ./demo/output
# 示例输出：{"case_id":"demo","status":"insufficient_evidence","candidates":[]}
```

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] `result.json` 合法、含 `case_id`、`status` 合法；`output_contract` 全部字段无缺键，未知键不改名不删除
- [ ] 每条候选有 `provenance`（库—查询—原始响应—映射—去重—合并可回溯），`raw_snapshot` 为逐字片段
- [ ] 每条候选有 `field_sources`，关键字段（title/journal/year/doi）按各库真实语义填充
- [ ] 去重可解释：DOI 精确 > PMID/PMCID 精确 > 标题+一作+年份 > 标题模糊（≥0.90 且同年±1）；`duplicates_merged` 与实际一致
- [ ] 合并优先级与冲突消解可解释，冲突写入 `conflicts`，不静默覆盖
- [ ] `coverage` 列明每库 `fields_missing`、仅单库记录、系统性缺失字段
- [ ] `evidence_records` 非空（有候选时），`source_file` 与 `locator` 齐全，`quote` 为逐字片段
- [ ] 同输入重复运行结果确定（`selftest.py --self-test` 含确定性场景）
- [ ] 空输入/坏 JSON/坏编码/缺 case/超大文件/缺 pdftotext 均不崩溃且如实标注
- [ ] 无伪造：缺失填 `null`/`not_reported`，缺口写 `limitations`/`degradations`/`pending_human`

## 7. 降级策略与边界

缺数据/缺软件必须如实标注，禁止编造：某库响应缺失→该库不进 `coverage`，`limitations` 注明未覆盖；字段缺失→`null`+`missing_metadata`；标识符/年份冲突→`conflicts`；文件 >2MB→跳过并记 `degradations`；缺 `pdftotext`→PDF 仅列路径；无候选→`insufficient_evidence` 并列缺口。不虚构 DOI/PMID/引文/年份/命中数/评测分数；不以示例填充充当正式结果。

本版明确取舍：把「输出契约逐字段对齐」作为第一优先级——`output_contract` 动态解析并原样回填，`change_log` 从骨架起全程记录每一次落盘与仲裁，保证评分方按契约逐键核对时零缺键、可追溯。
