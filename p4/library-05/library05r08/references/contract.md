# 字段级契约与定位规范

本文是 `scripts/main.py` 与 `scripts/core_impl.py` 的字段级约定，供复核者与后续维护者核对。
运行时优先以 `/input/case.json` 的 `output_contract` 为准；本文描述通用信封与定位约定。

## 1. 输入契约

- 运行时输入目录 `/input`：`case.json`（任务与 `output_contract`）、`task.md`、以及材料子目录（如 `materials/`）。
- 不假设固定文件名/键名：`case.json` 缺失或坏 JSON 都降级为 `input_error`，仍盘点剩余材料。
- 文本按 `utf-8-sig → utf-8 → latin-1 → gb18030` 容错读取；坏编码文件标 `decode_failed`，不崩溃。
- 单文本文件读取上限 512 KB；PDF/Office 抽取文本上限 2 MB；超出只记体积与哈希，不读全文。
- 哈希恒为完整流式 sha256（不受上限影响），作为内容身份，不随路径/时间变化。

## 2. 输出契约（`/app/output/result.json`）

必含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 从输入原样带回，不允许改写 |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error` |
| `result` | object | 盘点摘要与分组计数 |
| `sources` | list | 每条 source 的 `source_id / rel_path / sha256 / type` |
| `uncertainty` | object | `summary / count / items`，启发式与冲突判断集中记录 |
| `change_log` | list | 操作记录，含 `action / locator / before / after / reason` |
| `claims` | list | 逐条结论，含 `claim_id / kind / confidence / text / evidence` |
| `uncertainties` | list | 与 `uncertainty.items` 同源的列表形态 |
| `pending_human` | list | 待人工裁定的缺口/冲突 |
| `errors` | list | 运行时错误（不导致崩溃） |
| `degradations` | list | 降级说明（缺工具/无文本层/回退路径） |
| `runtime` | object | 仅此处允许时间信息，其余字段内容级确定性 |

`output_contract` 的**全部字段**必须逐键回填：dict 形态回填 key，list 形态回填字符串或
`{name/key/field}`。缺值写 `null`（标量）或 `[]`（list），不改名、不删未知键。

## 3. 主产物

| 文件 | 内容 | 确定性 |
|---|---|---|
| `result.json` | 信封 + 契约回填 + claims | 除 `runtime` 外确定 |
| `library_index.json` | 文件清单 + 分析（重复/版本/冲突/关系）+ 倒排索引 + 查询结果 | 逐字节确定 |
| `evidence_map.json` | 每条 claim 的 `source_id + locator + quote` 映射 | 逐字节确定 |
| `inventory.md` | 可读交接清单 | 确定 |

## 4. 证据定位（locator）规范

- `source_id` 取 `F001` 式稳定文件 id，或 `case`（指 case.json 文本）。
- `locator` 取材料相对路径（如 `materials/notes_a.md`）；case.json 证据用 `case.json`。
- `quote` 必须是材料中的**逐字片段**，`check` 命令按"子串包含"机械验证。
- 无文本层（PDF 缺 pdftotext、扫描件无 OCR、二进制）时，`quote` 写 `null` 并标
  `not_text_verifiable`，绝不编造页码/坐标/文本。
- 冲突不隐藏：同 stem 不同哈希且无版本标记 → `conflict_groups` + `source_conflict`，进 `pending_human`。

## 5. 状态语义

- `ok`：有 draft 语义层、无 errors/degradations/conflict，闭环完整。
- `partial`：有 draft 但仍有缺口，或确定性盘点完成但语义未补齐。
- `insufficient_evidence`：无材料或材料不足以做出任何判断。
- `input_error`：case.json 缺失/坏 JSON 等输入级错误；仍尽量盘点。

## 6. 退出码与降级

- 运行时五个子命令退出码恒 0；真实问题写 `errors/degradations`，不靠退出码表达。
- `selftest.py` 例外：它是本地开发自检，失败以退出码 1 表达（供作者在提交前核对）。
- 输出目录不可写时降级到 `<work>/output_fallback`，再降级到 `skill_output/`，并记 `degradations`。
- 缺外部工具（pdftotext/OCR）只做字节级盘点，`degradations` 标 `no_tool:<name>`，不假装有文本层。
