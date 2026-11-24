# 字段级契约与语义

本文是 `SKILL.md` 的补充规范，定义 `result.json` 各字段的精确含义、定位规范与置信度/状态语义。脚本按此实现，评测按此复核。

## 1. 定位规范（locator）

- 文件级定位统一使用 `rel_path`，即相对 `/input` 的正斜杠路径（如 `materials/paper.pdf`）。
- 行级定位使用 `rel_path#L<行号>`（如 `materials/admin_notes.md#L12`），行号从 1 开始。
- 文件身份证据为 `rel_path + sha256` 二元组；同一 `rel_path` 不同哈希视为内容变更，同一哈希不同 `rel_path` 视为副本/别名。
- 不输出绝对路径进交付产物（`work/library.json` 中的 `root` 仅供运行时重建绝对路径，不进 `library_index.json`/`result.json`）。

## 2. 状态语义（status）

| 值 | 含义 | 触发条件 |
|---|---|---|
| `ok` | 闭环完成 | 有材料、无 input_error、无扫描错误、无未裁定冲突、draft 中查询均已答 |
| `partial` | 部分完成，缺口已显式列出 | 无 draft 或 draft 为空（`no_draft`）、存在未裁定冲突、存在未答查询、存在不可读文件 |
| `insufficient_evidence` | 材料不足 | 空输入目录或没有任何可盘点文件 |
| `input_error` | 输入异常但已降级 | `case.json` 缺失或坏 JSON（仍盘点其余材料，退出码 0） |

判定顺序：空文件列表 → `insufficient_evidence`；否则 `input_error` → `input_error`；否则扫描错误/无 draft/冲突/未答查询 → `partial`；否则 `ok`。

## 3. 置信度语义（confidence）

| 值 | 证据等级 | 来源 |
|---|---|---|
| `fact` | 字节级事实 | 相同 sha256、文件存在性、可读性探测 |
| `explicit` | 材料原文明写 | 行政备注/别名声明中的显式关系 |
| `heuristic` | 规则推断 | stem 归一化、版本标记、模式匹配 |
| `inferred` | 模型推断 | draft 中模型补充的语义判断 |

启发式与推断结论必须同步写入 `uncertainties`（含 `about`/`severity`/`basis`/`disposition`），不得只写结论不写不确定性。

## 4. result.json 信封字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 输入原样带回 |
| `status` | string | 见上表 |
| `result` | object | 摘要 + 文件清单 + 分组 + 查询答案 + 索引元数据 + 能力探测 + 元数据 |
| `claims` | list | 每条结论带 `claim_id/kind/confidence/text/evidence` |
| `uncertainties` | list | 启发式结论的不确定性项 |
| `uncertainty` | object | `summary/count/items`，汇总视图 |
| `sources` | list | 每个来源文件 `source_id/locator/kind/sha256/size/readable` |
| `change_log` | list | 操作记录 `op/before/after/reason/locator`，无时间戳保证确定性 |
| `pending_human` | list | 需人工裁定/恢复的项 |
| `limitations` | object | `summary/data_gaps/tool_gaps/scope_exclusions/assumptions` |
| `errors` | list | 扫描/解析错误（handled=true） |
| `degradations` | list | 降级项（no_tool/no_draft/output_fallback/oversize 等） |
| `runtime` | object | `started/finished/wall_seconds/skill_version/commands` |

`output_contract` 的字段回填到 result.json 顶层：已知键映射到确定性结果（file/duplicate/version/alias/conflict/relation/index/search/metadata/change），未知键保持 `null`。

## 5. 证据溯源

- `evidence` 项固定三元组：`source_id`（文件 id 或 `case`/`task`）、`locator`、`quote`（逐字片段）。
- `quote` 必须与材料逐字一致（`verbatim_hit`），或等于该文件的 `rel_path`（`locator_match`，用于分组身份证据）。
- 二进制/无文本层文件不能作为文本 quote 的来源；此时只记录 `rel_path` 定位，不虚构内容。
- `evidence_map.json` 对每条 claim 输出逐项 `verbatim_hit` 与 `check` 结论；任一未命中进入 `unverified`。

## 6. 确定性保证

- 排序统一按 `rel_path`/`group_id`/`term` 字典序，不用 `os.walk` 默认顺序。
- 交付产物除 `runtime` 外不含时间戳；`runtime` 单独存放起止时间。
- 同输入两次运行：`library_index.json`/`evidence_map.json`/`inventory.md` 逐字节一致；`result.json` 删除 `runtime` 后一致。
