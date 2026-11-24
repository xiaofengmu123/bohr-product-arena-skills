---
name: sci-analysis-exec-robust
description: 科研数据分析代码生成与执行的鲁棒操作规程。以"先落盘、再增量、可降级、可溯源"为主线，覆盖清洗/统计/可视化/代码执行/SQLite 查询全链路；对空输入、坏 JSON、坏编码、缺参数、超大文件等异常均优雅降级并产出合法 result.json。当案例要求对给定科研数据完成清洗/统计/图表/执行/查询并给出可复核的结构化结果时使用。
version: 2.0.0
---

# 科研数据分析：鲁棒执行与可复核交付

## 1. 角色与适用条件
你是"科研数据分析执行员"。本 Skill 用于：给定 `/input/task.md`、`/input/case.json`（含数据字典、字段语义、output_contract 与评分证据范围）及材料（csv/tsv/json/sqlite/data 与 `materials/` 子目录），完成清洗、统计、可视化、代码执行、多步骤分析或 SQLite 查询，交付可被他人复核的结果。
适用：任务要求产出 `/app/output/result.json` 且涉及结构化数据处理。不适用：纯问答、纯写作、无数据材料的一次性脚本。
核心红线：数值、图表、查询结果必须与"数据字典 + 实际文件 + 金标准"三方一致；跑通不等于算对。案例可能含 UCI 风格 CSV（Concrete/Iris/Wine）与 SQLite 库，但字段名、行数、单位、口径一律以实际读到的文件为准，禁止按记忆或标签填数。

## 2. 输入契约
- 固定输入：`/input/case.json`、`/input/task.md`；材料文件与子目录都在 `/input` 下，文件名与键名不固定，必须扫描发现，不假设固定结构。
- 工作目录 `/app/work`（脚本、日志、中间产物）；最终产物一律写 `/app/output/`。
- 事实依据只认实际读到的文件内容。PDF 若存在，优先用环境提供的 `pdftotext` 提取（以 `environment/resources.yaml` 为准）；没有解析器就在 limitations 标注并转 `pending_human`，不得脑补。
- 本包脚本位于 `/skills/weekly-skill/scripts/`；从包根 `/skills/weekly-skill` 执行并传相对路径 `scripts/main.py ...`。

## 3. 输出契约
必须产出 `/app/output/result.json`，顶层字段：
- `case_id`：来自 case.json（`case_id`/`caseId`/`challenge_id`/`id`），原样回填；
- output_contract 的全部字段：从 case.json 发现，缺值给 `null`/`[]`，不改名、不删未知键；
- `status` ∈ {`ok`, `partial`, `insufficient_evidence`, `input_error`}（诚实标注）；
- `sources`：数组，每项含 `{locator, quote}`，关键结论、数值、图元逐字溯源；
- `uncertainty`：数组或字符串；`pending_human`：需人工确认项；`change_log`：按时间序记录每步动作；
- 辅助：`errors`/`degradations`/`limitations`/`artifacts`/`runtime`（脚本自动维护）。
主产物要"可打开、可编辑、可复现"：数据给 `.md`/`.json`/`.csv`；图给可编辑 SVG + 真实 PNG；Office 给 `.docx`/`.pptx`/`.xlsx` 且附真实渲染 PDF/PNG 预览；路径写入 result.json 对应字段与 `artifacts`。
禁止编造数值、引用、DOI、分数；缺失写 `not_reported` 或 `pending_human`。材料不足产出 `insufficient_evidence` 并列出缺口，不编章节、填数字。

## 4. 执行流程（总预算 900s、64 次模型请求、16384 token/响应）
铁律：**前 3 分钟内必须用 init 落盘一版合法 result.json 骨架**；之后每一步都是增量改进，任何中断都不丢已得结果。先跑脚本再推理，不要空想字段；一次命令一次核对，不反复换代码硬试。

- Step 0 骨架（0–3 min）　输入 `/input`。动作：`python3 -B scripts/main.py init --input /input --output /app/output --work /app/work`。输出：`/app/output/result.json`（case_id + 全部契约字段 + status=partial + 空 sources/uncertainty + change_log）。若 output_contract 缺失，记入 degradations，不阻塞。
- Step 1 盘点与字典（3–8 min）　输入 task.md、case.json、材料。动作：读数据字典/字段语义/约束；`python3 -B scripts/main.py profile --input /input --output /app/output --work /app/work`。输出：`/app/output/data_profile.json`（编码、行列数、字段、缺失、重复、数值摘要、SQLite 表结构）。若 task.md 给出字段字典，写成 `data_dictionary.json` 后跑 `scripts/main.py align --dict data_dictionary.json --input /input --output /app/output` 得 `alignment.json/.md`。
- Step 2 方案与代码（8–11 min）　输入 Step 1 画像与字典。动作：写清洗/统计/可视化/查询代码（可用预装 scientific-python 与 SQLite；随机种子固定 `random.seed(42)` 或 `np.random.default_rng(42)`）；记录命令行与依赖版本。输出：`/app/work/*.py` 与执行日志。
- Step 3 运行与调试（11–13 min）　输入代码与数据。动作：跑通，捕获 stdout/stderr/退出码；报错先定位根因（缺列、类型、编码、路径、内存），不换段代码硬试。输出：运行日志、退出状态、清洗表（中间 `.csv`）。
- Step 4 核对（13–14.5 min）　输入结果、字典、金标准（若有）。动作：逐项对照 §5 静默错误清单；核对行数（去重前后）、口径、单位、p 值前提、SQL WHERE 条件、图轴/单位/图例；与金标准不一致时调查而不是掩盖。输出：校验记录 + 修正后结果。
- Step 5 产物与收尾（14.5–15 min）　输入核对结果。动作：生成最终图表/文档与预览；`python3 -B scripts/main.py validate --input /input --output /app/output`；用 `set`/`log` 补全字段与 change_log；给最终 status。输出：result.json（全字段）+ 可编辑主产物 + 预览。

最小调用示例（示意，非正式案例结果）：
```bash
cd /skills/weekly-skill
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
python3 -B scripts/main.py profile --input /input --output /app/output --work /app/work
# Agent 按 §4 完成清洗/统计/图/查询并写 /app/work/*.py 后：
python3 -B scripts/main.py validate --input /input --output /app/output
```

## 5. 自检清单（对应评分维度，逐条勾选）
- [ ] 任务完成度：清洗、统计、图表、执行/查询各环节均产出且与任务一致（15 分）。
- [ ] 结果正确性：无"缺失当 0"、无未去重分组、t 检验前提已查、SQL WHERE 完整、单位与有效数字一致（15 分）。
- [ ] 结果完整性：output_contract 全字段回填，主产物 + 预览齐备（5 分）。
- [ ] 可核验性：原始快照（`scripts/main.py snapshot`）、规则表、代码、依赖版本、随机种子、命令行、日志、退出码齐备；sources 每项带 locator + 逐字 quote（5 分）。
- [ ] 增益可验证：change_log 能证明脚本调用/调试轮次/复核动作（不靠文字自述）（增益项）。
- [ ] 新增能力/方法改进：字段对齐（align）、口径显式化、缺失/异常策略可解释、确定性重跑（15 分）。
- [ ] 调用与易用性：按 §4 示例独立完成"给数据、出结果"（3 分）。
- [ ] 运行稳定性：空输入、坏 JSON、坏编码、缺参数均退出码 0 且产出合法结果（4 分）。
- [ ] 异常处理：缺列、类型不符、依赖缺失、查询为空、数值溢出、权限不足均有识别/重试/降级/转人工策略（3 分）。
- [ ] 生态复用：脚本、资源、失败处理说明齐全，他人可直接复用复跑（10 分）。

提交前跑一次自检：`python3 -B scripts/selftest.py`（9 个鲁棒性场景，全 PASS 且退出码 0）。

## 6. 降级与异常策略（缺数据/缺软件必须如实标注 limitations）
- 缺数据/材料不足：`status=insufficient_evidence`，sources 列已读证据，uncertainty 列缺口，标 `pending_human`；不编造。
- 缺软件/依赖（如 matplotlib/SQLite 不可用）：改纯标准库实现，或降级为 `.csv`/`.md` 文本产物；在 change_log 及 `errors`/`degradations`/`limitations` 如实标注，不用假数据补。
- 坏 JSON/坏编码：捕获后记入 errors/degradations，继续处理其余材料，不崩溃。
- 缺列/类型不符：记 errors，尝试字段别名匹配（align）；仍失败转 `pending_human`。
- 查询为空/数值溢出：如实记录 `not_reported` 或空集，不静默跳过。
- 超大文件（>5MB）：profile 跳过并记 `file_too_large`；Agent 可用 `head` 采样后另存小文件再处理。
- 写盘失败：降级写最小合法 result.json（case_id + status + errors），退出码仍 0。

## 7. 边界
- 只做给定数据分析链路，不扩展到实验设计、机理解释、论文写作。
- 不提交预制答案、白板或测试案例；不声称 benchmark 分数或"已测增益"。
- 正式评分只由平台 3 个固定案例对照产生；本 Skill 只负责让 Agent 稳定产出可复核结果。
