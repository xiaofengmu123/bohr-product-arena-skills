# 输出契约与中间产物 schema

本文档定义本 Skill 的全部落盘结构。脚本由 `finalize` 统一渲染最终产物，Agent
只写中间产物，不直接改 `result.json`。

## 0. 目录约定

| 路径 | 含义 |
|---|---|
| `/input/` | 只读输入：`case.json`、`task.md`、科研材料（PDF/txt/md/csv/json） |
| `/app/work/` | 中间产物与机器状态 |
| `/app/output/` | 最终交付：`result.json`、`report.md` |

`--input/--output/--work` 可覆盖；缺省值即上表。脚本路径相对 Skill 包根
`/skills/weekly-skill`，用 `python scripts/xxx.py` 调用。

## 1. 中间产物（在 /app/work/）

### 1.1 manifest.json（discover 生成）

```json
{
  "schema_version": "2.0",
  "case_id": "…",
  "input_dir": "/input",
  "task": {"path": "task.md", "bytes": 1234},
  "materials": [
    {"name": "paper.pdf", "rel_path": "paper.pdf", "kind": "pdf",
     "bytes": 12345, "text_path": "texts/paper.txt", "pages": 12,
     "degradations": []}
  ],
  "errors": [],
  "degradations": []
}
```

PDF 抽取文本按 `----- PAGE n / N -----` 分页标记；扫描版（抽出空文本）记
`scanned_pdf_empty -> needs_ocr`，缺工具记 `pdftotext_missing -> needs_ocr`。

### 1.2 plan.json（scaffold 生成）

审查计划：`case_id`、`facets`（六类 + 任务额外 facets）、`materials`、
`output_contract_keys`、`reading_queue`、`status`。Agent 依 `reading_queue`
逐段读原文，覆盖 `facets` 全部类别。

### 1.3 evidence/<EV-id>.json（Agent 逐条写入，一个文件一条）

原始证据条目（先抽取、不下结论）：

```json
{
  "id": "EV-001",
  "material": "paper.pdf",
  "locator": "p.3, para 2",
  "quote": "……（逐字摘录，可删省略号，不改词不增删关键数字）……",
  "kind": "claim|citation|number|method|norm",
  "category": "citation_support",
  "source_ref": "ref [12] 或 not_verified",
  "note": "可选：为何抽取此条",
  "review_status": "checked|needs_review"
}
```

`quote` 必须逐字；`locator` 可回查；`source_ref` 只在确实取得来源时填写。

### 1.4 findings/<F-id>.json（Agent 逐条写入，一个文件一条）

审查结论（引用 evidence id，不重复粘贴完整证据）：

```json
{
  "id": "F-001",
  "evidence_ids": ["EV-003", "EV-007"],
  "category": "citation_support",
  "finding_type": "unsupported_claim",
  "severity": "major",
  "verdict": "unsupported",
  "locator": "p.4, para 1",
  "quote": "……（结论处的逐字原文）……",
  "claim": "（转述被审查断言）",
  "basis": "（判定依据：引文语义 vs 结论语义）",
  "source_ref": "ref [12]",
  "suggestion": "（修改建议）",
  "confidence": "high|medium|low",
  "review_status": "checked|needs_review"
}
```

必填七键：`id / category / locator / quote / verdict / severity / suggestion`；
`evidence_ids` 至少一个（指向已存在的 evidence id）。`quote` 在 finding 中仍是
逐字原文（结论处），与 evidence 中的原文一起构成双向回查。

### 1.5 meta.json（scaffold 生成，Agent 收尾前编辑）

```json
{
  "schema_version": "2.0",
  "overall_verdict": "unchecked",
  "status": "partial",
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "limitations": [],
  "errors": [],
  "degradations": []
}
```

`status` ∈ `ok|partial|insufficient_evidence|input_error`；
`overall_verdict` ∈ `pass|issues_found|needs_review|unchecked|insufficient`。
`finalize` 会把 manifest 的 errors/degradations 并入 meta，再由 Agent 复核。

### 1.6 state.json（脚本管理，Agent 只读）

`started_at`、`change_log`（scaffold/finalize 事件）、`runtime`
（`budget_seconds:900`、`model_requests_budget:64`、`wall_seconds`、
`completed_at`）。`finalize` 首次运行时固化 `wall_seconds`，重复运行不改变。

### 1.7 audit_model.json（finalize 生成）

单一中间事实源：`plan + evidence + findings + meta + runtime`。它先于
`result.json` 生成，`result.json` 只是它的渲染视图。人工复核可只读
`audit_model.json` 重建全部结论与证据链。

## 2. 最终产物（在 /app/output/）

### 2.1 result.json

顶层字段：

- `case_id`：案例标识。
- `status`：`ok|partial|insufficient_evidence|input_error`。
- `summary.verdict`：`pass|issues_found|needs_review|unchecked|insufficient`；
  `summary.counts`：`total/critical/major/minor/suggestion` 与 `by_category`。
- `review_report.findings`：逐条问题（见 §1.4）。
- `evidence.ledger`：全部 evidence 条目；`evidence.evidence_map`：每条 finding
  的 `finding_id + locator + quote + basis + source_ref + evidence_ids`。
- `sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、
  `degradations`、`change_log`、`runtime`、`audit_model_ref`。

**output_contract 回填规则**：若 `case.json` 含 `output_contract`，其全部键
**原样回填到 result.json 顶层**（缺值给 `null`/`[]`/`""`，不改名、不删未知键）；
标准字段与契约字段同名时，标准字段优先（防止契约覆盖诚实的 `status`）。

### 2.2 report.md

人类可读、可编辑的报告：摘要、逐条 finding（定位/原文/转述/依据/来源/建议）、
以及 sources/uncertainty/pending_human/limitations/errors/degradations 清单。
由 `finalize` 从 `audit_model.json` 确定性渲染。

## 3. 最小合法结果（scaffold 即产生）

空输入、坏材料、时间不足等任何情况，`scaffold` 都先写一版
`status=partial, verdict=unchecked, findings=[]` 的合法 result.json，再逐步
增量改进。空材料最终应把 `status` 改为 `insufficient_evidence`、
`verdict` 改为 `insufficient`，并在 `limitations` 列出缺口，绝不编造结论。
