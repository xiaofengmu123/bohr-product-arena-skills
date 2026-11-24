# 字段与规则（fields-and-rules）

本文件定义机械层的稳定字段、判定规则与降级矩阵。脚本与 Agent 共用同一套命名，
保证 result.json 与各可编辑产物可复核、可复跑。所有规则均为默认值，可用命令行覆盖。

## 1. 字段规范

### result.json 顶层（保留键，回填契约时不得覆盖）

| 字段 | 类型 | 含义 |
|---|---|---|
| `case_id` | string | 案例标识，从 case.json/task.md 识别 |
| `status` | enum | `ok / partial / insufficient_evidence / input_error`，必须诚实 |
| `task_id` / `task_title` | string | 任务标识与标题 |
| `output_contract` | object | 题目 output_contract 原样回显（缺省时用默认契约） |
| `sources` | list | `{item, locator, quote}` 逐字证据；冲突并列并标 `source_conflict` |
| `uncertainty` | list | `{item, reason, severity}` |
| `change_log` | list | `{time, actor, step, detail}` 操作时间线 |
| `errors` / `degradations` | list | 失败与降级记录 |
| `limitations` | list | 已知能力边界，不得隐瞒 |
| `pending_human` | list | 需要人工裁决的事项 |
| `runtime` | object | 起止时间、耗时、工具链 |
| `computed` | object | 机械证据：files/summary/duplicate_groups/version_candidates/version_notes/aliases/reconciliation/answers/index/artifacts |
| `artifacts` | object | 可编辑产物文件名 → 文件名映射 |

### 文件清单条目（library_manifest.csv / file_manifest / metadata）

`path, role, type, size, mtime, mode, nlink, extension, encoding, sha256, hash_status, link_target, broken, readable`

- `role`：`input_contract`（case.json/task.md）或 `material`。
- `type`：`file / symlink / non_regular`。
- `hash_status`：`ok / too_large / unreadable / read_error`；非文件类型无哈希。

## 2. 判定规则

- **去重**：按内容 SHA-256 分组，组内文件数 ≥2 记为 duplicate group；不按文件名。
- **版本候选**：归一化主干（去扩展名、版本尾缀、日期、副本标记）+ 组内两两内容相似度
  （difflib.SequenceMatcher）。相似度 < 0.5 或内容哈希相同仍需标注为启发式。
- **别名**：符号链接（含断链，`link_target` + `broken`）与硬链接组（同 `(st_dev, st_ino)`）。
- **对账**：库目录记录与磁盘清单比对，输出 `matches / mismatches / missing / orphaned`。
  - `mismatches`：目录给出 sha256/size 且与磁盘不一致。
  - `missing`：目录有、磁盘无。
  - `orphaned`：磁盘有、目录未登记（仅 `role=material`）。
- **索引**：路径 + 归一化主干 + 关键词 + 文本预览；检索为大小写不敏感子串匹配，
  仅作机械层候选，语义层由 Agent 补判。

## 3. 降级矩阵（不许编造）

| 场景 | 处置 | 记录位置 |
|---|---|---|
| 输入目录不存在/为空 | 退出码 0，`insufficient_evidence` 或 `input_error` | status + errors |
| case.json 缺失/坏 JSON | `input_error`，仍盘点其余材料 | status + errors |
| output_contract 缺失 | 默认契约回填 + `uncertainty` | output_contract + uncertainty |
| 文件超大（>2MB 默认） | 跳过内容哈希，保留路径/大小/权限 | hash_status=too_large |
| 符号链接/非常规文件 | 不哈希，保留链接目标与类型 | type/link_target + degradations |
| 不可读文件 | 不哈希，`readable=false` | hash_status=unreadable |
| 坏编码 | utf-8-sig→utf-8→latin-1 逐级降级，标记 encoding_fallback | encoding + degradations |
| SQLite 打不开/坏表 | 记 `sqlite_open_error` 类信息，不崩溃 | errors/degradations |
| 写盘失败 | 保留已落盘骨架，退出码 2 | errors + 退出码 |

## 4. 时间预算与早落盘（本版侧重点）

- 总预算：900 秒 / 64 次模型请求（见 `SKILL.md` 第 4 节阶段表）。
- 骨架必须在前 180 秒内落盘：`python3 -B scripts/main.py --skeleton-only`。
- 之后每步只做增量：`--update patch.json --result /app/output/result.json`。
- 看板：`/app/work/budget.json` 记录阶段预算与 checkpoint；`--budget` 打印剩余进度。
- 任何一步超时或中断，先保留当前 result.json 与已有产物，再如实标注 limits。
