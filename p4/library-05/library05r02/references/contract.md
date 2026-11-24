# 字段级契约与定位规范

## 1. 状态语义（result.json.status）

| status | 含义 |
|---|---|
| `ok` | 盘点 + 语义解释齐全，无降级与待办 |
| `partial` | 已交付部分结果，含降级或待办（如 `no_draft`） |
| `insufficient_evidence` | 输入为空或材料不足，无文件可盘点 |
| `input_error` | case.json 缺失/坏 JSON 且无材料，或输入本身不可用 |

## 2. result.json 顶层字段

- `case_id`：从输入原样带回，禁止改写。
- `output_contract` 字段：dict/list 两种形态逐键回填；缺失键回填 `null`（未知）或确定性产物值。
- `status`：见上表。
- `result`：`summary`（文件数、字节数、别名/版本/冲突计数、检索命中数）、`groups`
  （alias_groups/version_groups/conflicts/explicit_relations）、`query_results`。
- `claims`：逐条结论，`claim_id` 唯一，`confidence` ∈ {explicit, inferred, heuristic, high}。
- `sources`：文件清单（id、rel_path、name、sha256、size、type）。
- `evidence_map`：claim_id → evidence（source_id + locator + quote）。
- `change_log`：操作记录，含 op、status、locator、before/after/reason。
- `pending_human`：冲突、不可读、缺口等需人工裁定项。
- `errors` / `degradations`：输入/草稿错误与降级说明。
- `uncertainty`：启发式结论计数与清单；`uncertainties` 为其 items 同义副本。
- `runtime`：预算、已用秒、各阶段耗时；**唯一允许非确定性的字段**。

## 3. 定位规范（locator）

- 材料文件：`rel_path`（相对 `/input` 的斜杠路径），如 `materials/paper.pdf`。
- 文本行：`rel_path#L<行号>`。
- 配置来源：`case.json`、`task.md`；结构化字段用 `case.json#<path>`。
- 每条 evidence 必须同时含 `source_id`、`locator`、`quote`；`quote` 为材料中**逐字片段**
  （允许空白差异），由 `check` 机械校验命中。

## 4. 确定性

- `library_index.json`、`evidence_map.json`、`inventory.md` 不含 `mtime`、不含运行时；
  JSON 用 `sort_keys=True`、`ensure_ascii=False`、2 空格缩进。
- 同输入两次运行，除 `result.json.runtime` 外逐字节一致。

## 5. 契约回填规则

- `output_contract` 为 dict：按其键名回填；未知键 `null`。
- `output_contract` 为 list：逐项取 `name/key/field/id` 作为键名；无则整项作键。
- 兜底键集合（缺 output_contract 时）：`inventory, metadata, versions, duplicates,
  conflicts, operations, change_log, search, evidence_map, sources`。
- 键名别名映射：`files→inventory`、`queries/questions/answers→search`、`logs/ops→operations`。
