# 降级与诚实标注规则（Degradation Rules）

本文件是 SKILL.md 第 8 节的展开。原则：**缺什么如实记什么，不补造**。任何降级都要落到 `errors`/`degradations`/`uncertainty`/`pending_human` 中的具体条目，并写清影响范围。

## 1. 输入降级

| 场景 | status | 处置 |
|---|---|---|
| 输入目录为空或无任何可读文件 | `insufficient_evidence` | 在 `uncertainty`/`pending_human` 列出缺口，不编造方案、数值、结论 |
| `case.json` 无法解析（坏 JSON/坏编码） | `input_error` | 记入 `errors`，继续处理其余材料；`case_id` 退化为文件名派生或 `unknown` |
| 单文件超 2MB | 保持当前 status | 记入 `degradations`，跳过该文件，其余材料继续 |
| 缺页/缺 PDF 文本层 | 保持当前 status | 记入 `degradations` 并标注"该证据未能读取"，不得假装已读 |

## 2. 软件降级

- PDF 解析不可用 → `degradations` 注明"该证据未能读取"，不得在证据里引用未读页。
- `scripts/*.py` 相对路径不可解析 → 回退 `/skills/weekly-skill/scripts/<name>.py`，不影响结果字段。
- 写盘失败 → 降级为最小合法 JSON（含 `case_id` + `status` + `errors`），进程退出码 0。

## 3. 判定降级

- 伦理/安全/设备可用性未经审批或无法确认 → `unconfirmed` + `needs_expert=true`，绝不写成可执行事实。
- 同一指标两个来源不一致 → 两条都记录，标 `verdict="conflict"` 或 `notes` 注 `source_conflict`。
- 参数/时间/样本/成本任一数值缺失 → 相应算术项记 `missing`，不猜测数值补位。

## 4. 禁止事项

- 不虚构引用、DOI、数值、价格、审批状态、评测分数。
- 不预写正式答案、不用示例填充代替真实材料审查。
- 不把"方案没写"当"方案满足"。
- 不用固定 schema 强行改名/删键；契约字段必须原样回填。

## 5. 增量与复跑

- 每次写盘追加 `change_log`（`step/action/reason` + 内容哈希），证明增量而非覆盖。
- 相同输入相同口径可复跑：所有脚本函数为纯函数，不依赖随机数与墙钟时间。
- 自检 `python3 scripts/selftest.py` 在 `insufficient_evidence`/`input_error` 状态下，内容类检查降为 WARN，结构类检查仍为 FAIL 判定。
