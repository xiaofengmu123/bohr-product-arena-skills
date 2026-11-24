# 字段级契约与定位规范

本文是 `SKILL.md` 的补充实现说明，供他人复核与二次开发。运行时输入输出路径以 `/input`、`/app/work`、`/app/output` 为默认值，均可用 `--input/--work/--output` 覆盖。

## 1. 输入契约

- `/input/case.json`：任务与 `output_contract`。可能缺失、为空或为坏 JSON；`output_contract` 可能是 `dict` 或 `list` 两种形态。
- `/input/task.md`：题目说明（题面）。
- `/input/` 递归下的其余文件：真实材料（文本、JSON、PDF、Office、图片、归档等），**不假设固定文件名或目录层级**。
- 原则：先发现，再假设。文本按 `utf-8-sig` 优先、`utf-16`、`latin-1` 顺序容错解码；坏 JSON、坏编码、不可读文件都降级并记录，不崩溃。

## 2. 输出契约：`/app/output/result.json`

必须包含：

| 字段 | 语义 |
|---|---|
| `case_id` | 从输入原样带回，不允许被改写；找不到则 `unknown` |
| `output_contract` 的全部字段 | dict/list 形态都支持，逐键回填，缺值 `null`/`[]`，不改名、不删未知键 |
| `status` | `ok \| partial \| insufficient_evidence \| input_error` |
| `result` | 盘点摘要（文件/目录数、分组数、检索问题数等） |
| `claims` | 逐条结论，每条带 `claim_id/kind/confidence/text/evidence` |
| `uncertainties` | 不确定项数组；`uncertainty` 为其摘要对象 |
| `sources` | 文件来源清单（`source_id + rel_path + sha256 + mode + kind`） |
| `change_log` | 操作记录，含 `before/after/reason/locator` |
| `pending_human` | 待人工裁定项（冲突、不可读文件、缺口） |
| `errors` / `degradations` | 错误与降级说明 |
| `runtime` | 引擎版本与耗时（**允许随环境变化**） |

状态判定顺序（确定性）：

1. 输入目录不存在或为空 → `insufficient_evidence`；
2. `case.json` 缺失但有材料 → `input_error`（仍盘点材料）；
3. `case.json` 存在但坏 JSON → `input_error`；
4. 未提供 `work/draft.json` → `partial`（`degradations` 标 `no_draft`）；
5. 存在未解决冲突或不可读文件 → `partial`；
6. 其余 → `ok`。

## 3. 其他产物

| 文件 | 内容 |
|---|---|
| `library_index.json` | 完整盘点：文件清单 + 分析（别名/版本/冲突/显式关系/问题/提示），**不含 runtime** |
| `evidence_map.json` | `claim_id -> {text, evidence[]}`，每条 evidence 含 `locator + quote` |
| `inventory.md` | 可读交接清单 |

## 4. 溯源硬要求

- 每条结论（重复、版本、别名、冲突、检索命中、跳过）都要能指回材料的逐字片段：`source_id + locator + quote`。
- 冲突信息不隐藏：同一 stem 多个哈希且无显式版本标记时，标 `source_conflict` 并进 `pending_human`，不擅自裁定。
- 无文本层（PDF/扫描件/二进制）时，quote 允许为 `rel_path` 定位 + `no_text_layer` 标记，且 `check` 明确提示“核心无法复验逐字命中”，不假装有文本层。
- 缺值写 `pending_human` 或 `not_reported`，不虚构引用、DOI、数值。

## 5. 确定性规则

- 文件遍历与分组一律按 `rel_path.as_posix()` / stem / ID 排序，不依赖目录返回顺序。
- JSON 与文本产物统一 `ensure_ascii=False + indent=2 + newline="\n"` 写出，跨平台逐字节一致。
- `library_index.json` / `evidence_map.json` / `inventory.md` 不含时间戳、绝对路径或随机数。
- 同输入两次运行，除 `result.json.runtime` 外逐字节一致（由 `scripts/selftest.py` 验证）。
- 不记录 mtime、不依赖文件系统顺序，保证可复跑。

## 6. 降级表

| 场景 | 处置 |
|---|---|
| 材料不足 / 无文本层 | `status=insufficient_evidence`，缺口写 `pending_human` |
| 缺外部工具（pdftotext/OCR/office） | 只做字节级盘点，`degradations` 标 `no_tool:<name>`，不假装有文本层 |
| 加密/损坏/权限不足/超大文件 | `errors`/`degradations` 逐条记录，文件标 `unreadable`/`hash_failed` |
| 坏 JSON / 坏编码 | 捕获记录到 `errors`，继续处理其余材料，退出码仍 0 |
| 启发式版本/别名分组 | `confidence=heuristic` + `uncertainty`；显式行政版本备注（`explicit`）可覆盖 |
| 输出目录不可写 | 依次降级 `<work>/output_fallback`、`skill_output/`，记 `degradations` |
| 无 draft 直接 build | `status=partial`，`degradations.no_draft`；仅交付确定性盘点结果 |

**退出码恒为 0**：真实问题写进 `errors`/`degradations`，不靠退出码表达。
