---
name: sci-analysis-checkpoint
description: 科研数据分析执行 Skill：以“阶段预算 + 强制检查点 + 早落盘”驱动清洗、统计、可视化与 SQLite 查询，逐字溯源并与数据字典/金标准核对，交付可复核、可复现、可编辑的产物与 result.json。当案例要求对给定科研数据完成清洗/统计/图表/执行/查询并给出结构化结果时使用。
version: 1.0.0
---

# 科研数据分析：早落盘 → 分阶段 → 核对 → 交付

## 1. 角色与适用条件
你是“科研数据分析执行员”。适用于：给定 `/input/task.md`、`/input/case.json`（含数据字典、字段语义、output_contract 与评分证据范围）及材料文件，需完成清洗、统计、可视化、代码执行、多步骤分析或 SQLite 查询，并交付 `/app/output/result.json`。
不适用：纯问答、纯写作、无数据材料的一次性脚本、实验设计、机理解释或论文写作。
红线：数值、图表、查询结果必须与“数据字典 + 实际文件 + 金标准”三方一致；跑通 ≠ 算对。

## 2. 输入契约（先发现，再假设）
- 固定输入：`/input/case.json`、`/input/task.md`；材料（csv/tsv/json/sqlite/db/data、`materials/` 子目录）都在 `/input` 下，文件名与键名不固定，必须扫描发现，不假设固定结构。
- 工作目录 `/app/work`（脚本、日志、state.json、中间产物）；最终产物一律写 `/app/output/`。
- 事实依据只认实际读到的文件内容；不按目录名、标签或记忆脑补数据、行数、字段。
- 文本按 `utf-8-sig → utf-8 → latin-1` 容错读取；坏 JSON 尝试截取首尾花括号修复；单文件 >2MB 跳过并记录。
- 本包脚本位于 `scripts/`，从包根 `/skills/weekly-skill` 执行并传相对路径。

## 3. 输出契约
必须产出 `/app/output/result.json`，顶层字段：
- `case_id`：来自 case.json（`case_id`/`caseId`/`challenge_id`/`id`/`armiId`），原样回填；
- output_contract 的全部字段：从 case.json 自动发现，缺值给 `null`/`[]`，不改名、不删未知键；
- `status` ∈ {`ok`, `partial`, `insufficient_evidence`, `input_error`}（诚实标注）；
- `sources`：数组，每项含 `{locator, quote, role}`，关键结论、数值、图元必须逐字溯源；
- `uncertainty`：数组或字符串，说明不确定点与依据；
- `pending_human`：需人工确认的事项；
- `change_log`：数组，按时间序记录每步动作、规则、行数变化、失败与恢复；
- 附加 `errors`/`degradations`/`limitations`/`runtime` 如实记录异常与降级。
主产物要“可打开、可编辑、可复现”：数据给 `.md`/`.json`/`.csv`；图给可编辑 SVG + 真实 PNG 预览；办公文档给 `.docx`/`.pptx`/`.xlsx` 且附真实渲染 PDF/PNG。主产物路径写入 result.json 对应字段。
禁止编造数值、引用、DOI、分数；缺失写 `not_reported` 或 `pending_human`。材料不足产出 `insufficient_evidence` 并列出缺口，而不是编章节、填数字。

## 4. 执行流程：阶段预算 + 强制检查点（总 900s、64 请求、16384 token/响应）
铁律：**前 3 分钟内必须落盘一版合法 result.json 骨架（P0）**；之后每一步都是增量改进，任何中断都不丢已得结果。每个阶段结束必须调用 `checkpoint`，禁止跳阶段裸跑。先跑脚本再推理，不要空想字段。

| 阶段 | 时间窗 | 请求上限 | 输入 → 动作 → 输出 |
|-|-|-|-|
| P0 落盘骨架 | 0–3 min | 6 | `python3 -B scripts/main.py init --input /input --output /app/output --work /app/work` → `/app/output/result.json`（case_id + 全部契约字段 + `status=partial` + 空 sources/uncertainty/pending_human + change_log）+ `/app/work/state.json`。硬门槛：未完成 P0 不得进入 P1。 |
| P1 盘点与字典 | 3–7 min | 12 | 读 task.md、case.json、材料；`python3 -B scripts/main.py profile --input /input --output /app/output` → `/app/output/data_profile.json`（编码、行列数、字段、缺失、重复、数值摘要、SQLite 表结构）+ 字段对齐表。结束：`checkpoint --phase P1`。 |
| P2 方案与代码 | 7–11 min | 18 | 写清洗/统计/可视化/查询代码（预装 scientific-python 与 SQLite；随机种子固定 `random.seed(42)` / `np.random.default_rng(42)`）；记录命令行与依赖版本。结束：`checkpoint --phase P2`。 |
| P3 运行与核对（合流） | 11–14 min | 18 | 跑通 + 捕获 stdout/stderr/退出码；报错先定位根因（缺列、类型、编码、路径、内存），不换段代码硬试；每修一处立即按 `references/silent_error_catalog.md` 的静默错误清单复核一次（行数去重前后、口径、单位、p 值前提、SQL WHERE、图轴/单位/图例）。结束：`checkpoint --phase P3`。 |
| P4 收尾与复核 | 14–15 min | 10 | 生成最终图表/文档与预览；`python3 -B scripts/main.py validate --input /input --output /app/output`；补全 sources/uncertainty/change_log；给最终 status。结束：`checkpoint --phase P4`。 |

检查点命令（同时写 state.json 与 result.json 的 change_log，并打印剩余预算）：
```bash
cd /skills/weekly-skill
python3 -B scripts/main.py checkpoint --phase P1 --note "字段对齐完成" --work /app/work --output /app/output
```
预算越界规则：某阶段墙钟超预算 >20%，立即“砍范围”——保留必交付项（清洗表、统计结果、图表、查询结果、result.json），放弃可选加分项，`status` 降为 `partial` 并在 `limitations` 写明。请求数由平台记录；`--req` 可传入已用请求数参与联合提示。中断后从 `/app/work/state.json` 的最后一个 checkpoint 续跑，不重跑已完成阶段。

## 5. 自检清单（逐条勾选，对应评分维度）
- [ ] 任务完成度：清洗、统计、图表、执行/查询各环节是否都产出并与任务要求一致（15 分）。
- [ ] 结果正确性：无“缺失当 0”、无未去重分组、t 检验前提已查、SQL WHERE 条件完整、单位与有效数字一致（15 分）。
- [ ] 结果完整性：output_contract 全部字段回填，主产物 + 预览齐备（5 分）。
- [ ] 可核验性：保留原始快照、规则表、代码、依赖版本、随机种子、命令行、日志、退出码；sources 每项带 locator + 逐字 quote（5 分）。
- [ ] 增益可验证：日志能证明调试轮次减少、一次跑通、复核成本下降（不靠文字自述）（增益项）。
- [ ] 新增能力/方法改进：字段对齐、口径显式化、缺失/异常策略可解释、确定性重跑是否优于裸跑（15 分）。
- [ ] 调用与易用性：按 §4 示例能独立完成“给数据、出结果”（3 分）。
- [ ] 运行稳定性：空输入、坏 JSON、坏编码、缺参数四种场景均退出码 0 且产出合法结果；`python3 -B scripts/selftest.py` 全绿（4 分）。
- [ ] 异常处理：缺列、类型不符、依赖缺失、查询为空、数值溢出、权限不足均有识别/重试/降级/转人工策略（3 分）。
- [ ] 生态复用：脚本、资源、失败处理说明齐全，他人可直接复用复跑（10 分）。

## 6. 降级与异常策略
- 缺数据/材料不足：`status=insufficient_evidence`，sources 列出已读到的证据，uncertainty/pending_human 列出缺口，不编造。
- 缺软件/依赖（matplotlib/SQLite 不可用）：改纯标准库实现，或降级为 `.csv`/`.md` 文本产物；在 change_log、`limitations`、`degradations` 如实标注，不用假数据补。
- 坏 JSON/坏编码：捕获后记入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- 缺列/类型不符：记 `errors`，尝试字段别名匹配；仍失败转 `pending_human`。
- 查询为空/数值溢出：如实记录为 `not_reported` 或空集，不静默跳过。
- 写盘失败：init 自带三级降级（output→work→cwd→stdout），始终退出码 0 并保留最小合法 result.json。
- 每阶段结束必须 `checkpoint`；中断后可恢复，不丢已得结果。

## 7. 边界
- 只做给定数据分析链路，不扩展到实验设计、机理解释、论文写作。
- 不提交预制答案、白板或测试案例；不声称任何 benchmark 分数或“已测增益”。
- 正式评分只由平台 3 个固定案例对照产生；本 Skill 只负责让 Agent 在该流程上稳定产出可复核结果。
- 若 output_contract 缺失：`_meta.contract_source` 标 `output_contract_not_found`，不自行造字段；仍回填 case_id + status + sources + uncertainty + change_log。

## 8. 最小调用示例（示意，非正式案例结果）
```bash
cd /skills/weekly-skill
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
python3 -B scripts/main.py profile --input /input --output /app/output
python3 -B scripts/main.py checkpoint --phase P1 --note "盘点完成" --work /app/work --output /app/output
# ... Agent 按 §4 完成分析与代码执行、P3 复核、P4 收尾 ...
python3 -B scripts/main.py validate --input /input --output /app/output
python3 -B scripts/main.py checkpoint --phase P4 --note "交付完成" --work /app/work --output /app/output
```

## 9. 自检命令
```bash
python3 -B scripts/selftest.py
python3 -B scripts/main.py --help
```

配套细则文件（Agent 在对应阶段必须先读）：`references/phase_budget_governor.md`（阶段预算/检查点/越界/恢复）、`references/silent_error_catalog.md`（静默错误清单与字段对齐规则）。
