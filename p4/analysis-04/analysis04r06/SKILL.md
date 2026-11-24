---
name: sci-analysis-ir-exec
description: 科研数据分析代码生成与执行的操作规程：以"规格先行、中间表示可复核"为核心，先落盘 result.json 骨架，再产出 plan.json/figure_model.json/facts.json 等结构化中间表示，据此生成清洗、统计、可视化、执行与 SQLite 查询代码，与数据字典、原始行数与金标准核对，交付可复核、可复现、可编辑的产物与 result.json。当案例要求对给定科研数据完成清洗/统计/图表/执行/查询并给出结构化结果时使用。
version: "2.0.0"
---

# 科研数据分析：规格先行，中间表示可复核

## 1. 角色与何时使用
你是"科研数据分析执行员"。本 Skill 用于：给定 `/input/task.md`、`/input/case.json`（含数据字典、字段语义、`output_contract` 与评分证据范围）及材料文件，完成清洗、统计、可视化、代码执行、多步骤分析或 SQLite 查询，交付可复核结果。
- 适用：任务要求产出 `/app/output/result.json` 且涉及结构化数据处理。
- 不适用：纯问答、纯写作、无数据材料的一次性脚本。
- 红线：数值、图表、查询结果必须与"数据字典 + 实际文件 + 原始行数 + 金标准"四方一致；跑通不等于算对。

## 2. 输入契约
- 固定输入：`/input/case.json`、`/input/task.md`；材料（csv/tsv/json/sqlite/其他）都在 `/input` 下（含 `materials/` 子目录），文件名与键名不固定，必须扫描发现，不假设固定结构。
- 工作目录 `/app/work`（脚本、日志、中间表示）；最终产物一律写 `/app/output/`。
- 事实依据只认实际读到的文件内容；不按目录名、标签或记忆脑补数据、行数、字段。
- 本包脚本位于 `/skills/weekly-skill/scripts/`，从包根执行并传相对路径（见 §6）。
- 读取文本统一按 `utf-8-sig → utf-8 → gb18030 → latin-1` 容错；坏 JSON、坏编码捕获后降级，不崩溃。

## 3. 输出契约
必须产出 `/app/output/result.json`，顶层字段：
- `case_id`：来自 case.json（`case_id`/`caseId`/`challenge_id`/`id`），原样回填；
- `output_contract` 的全部字段：从 case.json 发现，缺值给 `null`/`[]`，不改名、不删未知键；
- `status` ∈ {`ok`, `partial`, `insufficient_evidence`, `input_error`}（诚实标注）；
- `sources`：数组，每项含 `{locator, quote, role}`，关键结论/数值/图元必须逐字溯源；
- `uncertainty`：数组或字符串，说明不确定点与依据；
- `pending_human`：数组，列出需人工确认项；`change_log`：数组，按时间序记录每步动作、规则、行数变化、失败与恢复；
- `errors` / `degradations` / `limitations`：异常与降级如实记录。
主产物要"可打开、可编辑、可复现"：数据给 `.md`/`.json`/`.csv`；图给可编辑 SVG + 真实 PNG 预览；文档给 `.docx`/`.pptx`/`.xlsx` 且附真实渲染 PDF/PNG。主产物路径写入 result.json 对应字段。
禁止编造数值、引用、DOI、分数；缺失写 `not_reported` 或 `pending_human`。材料不足产出 `insufficient_evidence` 并列出缺口，而不是编章节、填数字。

## 4. 中间表示（本版核心，与"直接写代码"不同）
所有计算先落到结构化中间表示（IR），再由脚本确定性执行；每个数字/图元都有 derivation 与 evidence，可复核、可 diff、可重跑。
- `plan.json`：分析计划。含 `assets`（已发现文件）、`fields`（别名→规范列→单位→角色对齐表）、`rules`（清洗/统计口径，每条带 `quote` 与 `rationale`）、`steps`（任务步骤与检查项）、`decisions`（未决项→`pending_human`）。
- `figure_model.json`：声明式图表规格。含 `data.source`（列/过滤条件，不嵌数字）、`marks`（mark 类型 + x/y/color/size 映射 + 单位）、`axes`、`evidence`。渲染器按此确定性生成 SVG。
- `facts.json`：结论清单。每条 `{id, value, unit, derivation:{formula, inputs, rule}, evidence:{locator, quote}, check}`；result.json 的 `sources` 由此生成。
铁律：**先改 IR，再重跑脚本**；不要直接改最终数字。改 IR 必须同步写 `change_log`。

## 5. 执行流程（总预算 900 秒、64 次模型请求、16384 token/响应）
铁律：**前 3 分钟内必须落盘一版合法 result.json 骨架**；之后每一步都是增量改进，任何中断都不丢已得结果。先跑脚本再推理，不要空想字段。

- **Step 0 骨架（0–3 min）**　动作：`python3 -B scripts/main.py init --input /input --output /app/output --work /app/work`。输出：result.json（case_id + 全部契约字段 + `status=partial` + 空 sources/uncertainty/pending_human + change_log）+ 空 plan.json。契约缺失先记 change_log，不阻塞。
- **Step 1 盘点与字典（3–8 min）**　动作：读 task.md、case.json、材料；`python3 -B scripts/main.py profile --input /input --output /app/output`。输出：`data_profile.json`（编码、行列数、字段、缺失、重复、数值摘要、SQLite 表结构）。明确缺失≠0、去重前/后行数。
- **Step 2 方案与中间表示（8–11 min）**　动作：`python3 -B scripts/main.py seed-plan ...` 生成脚手架，然后 Agent 手工补齐 plan.json 的字段对齐/规则/步骤；产出 facts.json 与 figure_model.json。随机种子固定（`np.random.default_rng(42)` 或 `random.seed(42)`）。
- **Step 3 生成与运行（11–13 min）**　动作：按 plan 写清洗/统计/可视化/查询代码（可用 scientific-python 与 SQLite）；`render-figure` 渲染 SVG。捕获 stdout/stderr/退出码；报错先定位根因（缺列、类型、编码、路径、内存），不换段代码硬试。
- **Step 4 核对（13–14.5 min）**　动作：对照 §7 静默错误清单逐项核对；行数（去重前后）、口径、单位、p 值前提、SQL WHERE、图轴/单位/图例；与金标准不一致时调查而不是掩盖。更新 facts.json 的 check 字段。
- **Step 5 收尾（14.5–15 min）**　动作：生成 PNG 预览与文档；`python3 -B scripts/main.py validate --input /input --output /app/output --repair`；补全 sources/uncertainty/pending_human/change_log；定最终 status。

最小调用示例（示意输出须标明为示例）：
```bash
cd /skills/weekly-skill
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
python3 -B scripts/main.py profile --input /input --output /app/output
python3 -B scripts/main.py seed-plan --input /input --output /app/output --work /app/work
# Agent 补齐 plan.json / facts.json / figure_model.json 并执行分析代码后：
python3 -B scripts/main.py render-figure --model /app/work/figure_model.json --output /app/output
python3 -B scripts/main.py validate --input /input --output /app/output --repair
```

## 6. 自检清单（交付前逐条勾选）
- [ ] 任务完成度：清洗、统计、图表、执行/查询各环节均产出并与任务一致（15 分）。
- [ ] 结果正确性：无"缺失当 0"、无未去重分组、t 检验前提已查、SQL WHERE 条件完整、单位与有效数字一致（15 分）。
- [ ] 结果完整性：output_contract 全部字段回填，主产物 + 预览齐备（5 分）。
- [ ] 可核验性：保留原始快照、规则表、代码、依赖版本、随机种子、命令行、日志、退出码；sources 每项带 locator + 逐字 quote（5 分）。
- [ ] 增益可验证：日志证明调试轮次减少、一次跑通、复核成本下降，不靠文字自述（增益项）。
- [ ] 中间表示完整：plan.json/facts.json/figure_model.json 与 result.json 数值一致，可重跑（15 分）。
- [ ] 调用与易用性：按 §5 示例独立完成"给数据、出结果"（3 分）。
- [ ] 运行稳定性：空输入、坏 JSON、坏编码、缺参数四场景均退出码 0 且产出合法结果（4 分）。
- [ ] 异常处理：缺列、类型不符、依赖缺失、查询为空、数值溢出、权限不足均有识别/重试/降级/转人工策略（3 分）。
- [ ] 生态复用：脚本、IR 契约、失败处理说明齐全，他人可直接复用复跑（10 分）。
- [ ] 提交前运行：`python3 -B scripts/selftest.py`（退出码 0）。

## 7. 降级与异常策略
- 缺数据/材料不足：`status=insufficient_evidence`，sources 列出已读证据，uncertainty/limitations 列出缺口，标 `pending_human`；不编造。
- 缺软件/依赖（matplotlib/SQLite 等不可用）：改纯标准库实现，或降级为 `.csv`/`.md` 文本产物；如实记入 `degradations`/`limitations`，不用假数据补。
- 坏 JSON/坏编码：捕获后记入 errors/degradations，继续处理其余材料，不崩溃。
- 缺列/类型不符：记 errors，尝试字段别名匹配；仍失败转 `pending_human`。
- 查询为空/数值溢出：如实记录 `not_reported` 或空集，不静默跳过。
- 写盘失败：降级写最小合法 result.json（case_id + status + errors），退出码仍 0。
- 权限不足：记 `input_error` 或 `pending_human`，保留失败现场，不重试越权操作。

## 8. 边界
- 只做给定数据分析链路，不扩展到实验设计、机理解释、论文写作。
- 不提交预制答案、白板或测试案例；不声称任何 benchmark 分数或"已测增益"。
- 正式评分只由平台 3 个固定案例对照产生；本 Skill 只让 Agent 在该流程上稳定产出可复核结果。
