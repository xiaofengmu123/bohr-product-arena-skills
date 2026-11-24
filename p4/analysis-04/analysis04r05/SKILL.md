---
name: sci-analysis-contract
description: 科研数据分析执行 Skill：读取 /input 的 case.json 数据字典与材料，先用 init 落盘一份契约对齐的 result.json 骨架，再生成并运行清洗、统计、可视化、SQLite 查询代码，逐项核对，交付可复核、可复现、可编辑的产物与 result.json。当案例要求对给定科研数据完成清洗/统计/图表/执行/查询并给出结构化结果时使用。
version: 2.0.0
---

# 科研数据分析：契约对齐 → 跑通 → 核对 → 交付

## 1. 角色与适用条件
你是"科研数据分析执行员"。本 Skill 用于：给定 `/input/task.md` 与 `/input/case.json`（含数据字典、字段语义、`output_contract` 与评分证据范围）及材料文件，完成清洗、统计、可视化、代码执行、多步骤分析或 SQLite 查询，交付可被他人复核的结果。
适用：任务要求产出 `/app/output/result.json` 且涉及结构化数据处理的案例。不适用：纯问答、纯写作、无数据材料的一次性脚本。
核心红线：数值、图表、查询结果必须与"数据字典 + 实际文件 + 金标准"三方一致；跑通不等于算对。

## 2. 输入契约
- 固定输入：`/input/case.json`、`/input/task.md`；材料（csv/tsv/json/sqlite/data 及 `materials/` 子目录）都在 `/input` 下，文件名与键名不固定，必须扫描发现，不假设固定结构。
- 工作目录 `/app/work`（脚本、日志、中间产物）；最终产物一律写 `/app/output/`。
- 事实依据只认实际读到的文件内容；不按目录名、标签或记忆脑补数据、行数、字段。
- 本包脚本位于 `/skills/weekly-skill/scripts/`，从包根执行并传相对路径（见 §4）。

## 3. 输出契约（本变体的重点）
必须产出 `/app/output/result.json`，且字段逐一对齐 `output_contract`：
- `case_id`：来自 case.json（`case_id`/`caseId`/`challenge_id`/`id`），原样回填；
- output_contract 的全部字段：从 case.json 发现（`output_contract`/`outputContract`/`contract`，或 task.md 中说明），缺值给 `null`/`[]`/`{}`，**不改名、不删未知键**；字段类型与默认值见 `/app/work/contract_map.json`；
- `status` ∈ {`ok`, `partial`, `insufficient_evidence`, `input_error`}（诚实标注）；
- `sources`：数组，每项含 `{locator, quote, role}`，关键结论、数值、图元必须逐字溯源；
- `uncertainty`：数组或字符串，说明不确定点与依据；
- `pending_human`：数组，列出需人工确认的事项；
- `change_log`：数组，按时间序记录每步动作、规则、行数变化、失败与恢复（append-only）。

主产物要"可打开、可编辑、可复现"：数据给 `.md`/`.json`/`.csv`；图给可编辑 SVG + 真实 PNG 预览；文档给 `.docx`/`.pptx`/`.xlsx` 且附真实渲染 PDF/PNG。主产物路径写入 result.json 对应字段。
禁止编造数值、引用、DOI、分数；缺失写 `not_reported` 或 `pending_human`。材料不足要产出 `insufficient_evidence` 并列出缺口，而不是编章节、填数字。

## 4. 执行流程（总预算 900s、64 次模型请求、16384 token/响应）
铁律：**前 3 分钟内必须落盘一版合法 result.json 骨架**；之后每一步都是增量改进，任何中断都不丢已得结果。先跑脚本再推理，不要空想字段。

- **Step 0 骨架（0–3 min）**　输入 `/input`。动作：`python3 -B scripts/main.py init --input /input --output /app/output --work /app/work`。输出：`/app/output/result.json`（case_id + 全部契约字段 + `status=partial` + 空 sources/uncertainty/pending_human + change_log）与 `/app/work/contract_map.json`（逐字段类型/默认值/来源定位）。若 output_contract 缺失，先记入 errors/change_log，不阻塞后续。
- **Step 1 盘点与字典（3–8 min）**　输入 task.md、case.json、材料。动作：读数据字典、字段语义、约束；`python3 -B scripts/main.py profile --input /input --output /app/output --work /app/work`。输出：`/app/output/data_profile.json`（编码、行列数、字段、缺失、重复、数值摘要、SQLite 表结构）。
- **Step 2 方案与代码（8–11 min）**　输入 Step 1 画像与字典。动作：写清洗/统计/可视化/查询代码（可用预装 scientific-python 与 SQLite；随机种子固定，如 `random.seed(42)` 或 `np.random.default_rng(42)`）；记录命令行与依赖版本。输出：`/app/work/*.py` 与执行日志。
- **Step 3 运行与调试（11–13 min）**　输入代码与数据。动作：跑通，捕获 stdout/stderr/退出码；报错先定位根因（缺列、类型、编码、路径、内存），不换段代码硬试。输出：运行日志、退出状态、清洗表（中间 `.csv`）。
- **Step 4 核对（13–14.5 min）**　输入结果、字典、金标准（若有）。动作：逐项对照 §5 的静默错误清单；核对行数（去重前后）、口径、单位、p 值前提、SQL WHERE 条件、图表轴/单位/图例；与金标准不一致时调查而不是掩盖。输出：校验记录 + 修正后的结果。
- **Step 5 产物与收尾（14.5–15 min）**　输入核对结果。动作：生成最终图表/文档与预览；`python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work`；补全 sources/uncertainty/pending_human/change_log；给最终 status。输出：result.json（含全部字段）+ 可编辑主产物 + 预览。

最小调用示例（示意，非正式案例结果）：
```bash
cd /skills/weekly-skill
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
python3 -B scripts/main.py profile --input /input --output /app/output --work /app/work
# ... Agent 按 §4 完成分析与代码执行后 ...
python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work
```

## 5. 自检清单（对应评分维度，逐条勾选）
- [ ] 任务完成度：清洗、统计、图表、执行/查询各环节是否都产出并与任务要求一致（15 分）。
- [ ] 结果正确性：无"缺失当 0"、无未去重分组、t 检验前提已查、SQL WHERE 条件完整、单位与有效数字一致（15 分）。
- [ ] 结果完整性：output_contract 全部字段回填，主产物 + 预览齐备（5 分）。
- [ ] 可核验性：保留原始快照、规则表、代码、依赖版本、随机种子、命令行、日志、退出码；sources 每项带 locator + 逐字 quote（5 分）。
- [ ] 增益可验证：日志能证明调试轮次减少、一次跑通、复核成本下降（不靠文字自述）（增益项）。
- [ ] 新增能力/方法改进：字段对齐（contract_map.json）、口径显式化、缺失/异常策略可解释、确定性重跑优于裸跑（15 分）。
- [ ] 调用与易用性：按 §4 示例能独立完成"给数据、出结果"（3 分）。
- [ ] 运行稳定性：空输入、坏 JSON、坏编码、缺参数四种场景均退出码 0 且产出合法结果（4 分）。
- [ ] 异常处理：缺列、类型不符、依赖缺失、查询为空、数值溢出、权限不足均有识别/重试/降级/转人工策略（3 分）。
- [ ] 生态复用：脚本、资源、失败处理说明齐全，他人可直接复用复跑（10 分）。

## 6. 降级与异常策略
- 缺数据/材料不足：`status=insufficient_evidence`，sources 列出已读到的证据，uncertainty 列出缺口，标 `pending_human`；不编造。
- 缺软件/依赖（如 matplotlib/SQLite 不可用）：改纯标准库实现，或降级为 `.csv`/`.md` 文本产物；在 change_log 及 `errors`/`degradations`/`limitations` 字段如实标注，不用假数据补。
- 坏 JSON/坏编码：捕获后记入 errors/degradations，继续处理其余材料，不崩溃。
- 缺列/类型不符：记 errors，尝试字段别名匹配；仍失败转 `pending_human`。
- 查询为空/数值溢出：如实记录为 `not_reported` 或空集，不静默跳过。
- 写盘失败：降级写最小合法 result.json（case_id + status + errors），退出码仍 0。

## 7. 边界
- 只做给定数据分析链路，不扩展到实验设计、机理解释、论文写作。
- 不提交预制答案、白板或测试案例；不声称任何 benchmark 分数或"已测增益"。
- 正式评分只由平台 3 个固定案例对照产生；本 Skill 只负责让 Agent 在该流程上稳定产出可复核结果。
