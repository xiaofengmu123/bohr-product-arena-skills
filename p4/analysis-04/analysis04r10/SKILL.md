---
name: sci-analysis-recipe-engine
description: 科研数据分析的声明式配方执行规程：发现材料与数据字典，把清洗/统计/查询/绘图/校验写成类型化配方，由纯标准库引擎确定性执行并写入中间表示 state.json，先校验后交付，产出可复核的 result.json、可编辑 SVG+PNG 与清洗表。当案例要求对给定科研数据完成清洗/统计/图表/执行/查询并给出结构化结果时使用。
version: 1.0.0
---

# 科研数据分析：声明式配方 → 确定性执行 → 先校验后交付

## 1. 角色与何时使用
你是"科研数据分析执行员"。本 Skill 用于：给定 `/input/task.md` 与 `/input/case.json`（含数据字典、字段语义、`output_contract`、评分证据范围）及材料文件，完成清洗、统计、可视化、代码执行、SQLite 查询，交付可复核结果。适用：任务要求产出 `/app/output/result.json` 且涉及结构化数据的案例。不适用：纯问答、纯写作、无数据的脚本。
**核心红线：数值/图表/查询必须与"数据字典 + 实际文件 + 金标准"三方一致；跑通不等于算对。** 缺失值不得当 0、分组前去重、t 检验前提先查、SQL WHERE 写全、轴/单位/图例一致。

## 2. 输入契约
- 固定输入：`/input/case.json`、`/input/task.md`；材料（csv/tsv/json/sqlite/db/data 及 `materials/` 子目录）都在 `/input` 下，文件名与键名不固定，**必须扫描发现，不假设固定结构**。
- 工作目录 `/app/work`（state.json、中间 CSV、日志）；最终产物一律写 `/app/output/`。
- 事实依据只认实际读到的文件内容；不按目录名、标签或记忆脑补数据、行数、字段；PDF 用 `pdftotext` 读原文，无预解析层。
- 本包脚本位于 `/skills/weekly-skill/scripts/`，从包根执行并传相对路径（见 §4）。

## 3. 输出契约
必须产出 `/app/output/result.json`，顶层字段：
- `case_id`：来自 case.json（`case_id`/`caseId`/`challenge_id`/`id`），原样回填；
- output_contract 的全部字段：从 case.json 发现，缺值给 `null`/`[]`，不改名、不删未知键；
- `status` ∈ {`ok`, `partial`, `insufficient_evidence`, `input_error`}（诚实标注）；
- `sources`：每项 `{locator, quote, role}`，关键结论/数值/图元逐字溯源；
- `uncertainty`、`change_log`、`pending_human`、`errors`、`degradations`、`limitations`、`runtime`、`artifacts`、`checks`、`analysis`。
主产物"可打开、可编辑、可复现"：清洗表给 `.csv`；统计/查询给 `.json`；图给可编辑 `SVG` + 真实 `PNG` 预览（stdlib `zlib` 写出）；报告给 `.md`。主产物路径写入 result.json 对应字段。禁止编造数值/引用/DOI/分数；缺失写 `not_reported` 或 `pending_human`。

## 4. 执行流程（总预算 900s、64 次模型请求、16384 token/响应）
**铁律：前 3 分钟内必须落盘一版合法 result.json 骨架**；之后每一步都是增量改进，任何中断都不丢已得结果。所有子命令共享 `<work>/state.json` 中间表示，可断点续跑。先跑脚本再推理，不要空想字段。

- **Step 0 骨架（0–3 min）**　输入 `/input`。动作：`python3 -B scripts/main.py init --input /input --output /app/output --work /app/work`。输出：`result.json`（case_id + 全部契约字段 + `status=partial` + 空 sources/uncertainty + change_log）与 `state.json`。output_contract 缺失时记入 change_log，不阻塞。
- **Step 1 盘点与字典（3–8 min）**　动作：`discover`（扫描 manifest）→ `profile`（字段画像、编码、行列数、缺失、类型、SQLite 表结构）→ 读 task.md 数据字典与字段语义。输出：`/app/output/data_profile.json` + 字段对齐表（写入 state 的 `alignment`）。
- **Step 2 配方（8–11 min）**　动作：把清洗/统计/查询/绘图/校验写成声明式配方 `analysis_recipe.json`（schema 见 `references/recipe_schema.md`），用 `plan --recipe <work>/analysis_recipe.json` 载入。**不手写一次性 Python 作为基线**；引擎是确定性保底路径，Agent 可用预装 scientific-python 做更丰富分析并补记。随机种子固定 42。
- **Step 3 运行（11–13 min）**　动作：`python3 -B scripts/main.py run ...`，引擎执行配方：清洗（记录每步行数变化）→ 统计（Welch t、Pearson、groupby）→ SQLite 查询 → SVG+PNG。报错先定位根因（缺列/类型/编码/路径），不换段代码硬试。
- **Step 4 校验（13–14.5 min）**　动作：`check`（**先于 deliver** 校验契约完整性与内部一致性、对照金标准/预期值），逐项核对 §5 静默错误清单。不一致时调查而不是掩盖。输出：`/app/output/checks.json` + 修正后的 state。
- **Step 5 交付（14.5–15 min）**　动作：`deliver`（再次校验后写最终 result.json + 清洗表 + 统计/查询 JSON + 图 + report.md），补全 sources/uncertainty/change_log，给最终 status。

最小调用示例：
```bash
cd /skills/weekly-skill
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
python3 -B scripts/main.py discover --input /input --output /app/output --work /app/work
python3 -B scripts/main.py profile --input /input --output /app/output --work /app/work
# Agent 按 references/recipe_schema.md 写 <work>/analysis_recipe.json 后：
python3 -B scripts/main.py plan --recipe /app/work/analysis_recipe.json --input /input --output /app/output --work /app/work
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py check --input /input --output /app/output --work /app/work
python3 -B scripts/main.py deliver --input /input --output /app/output --work /app/work
```
`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`（可被 `INPUT_DIR/OUTPUT_DIR/WORK_DIR` 覆盖）。

## 5. 自检清单（对应评分维度，逐条勾选）
- [ ] 任务完成度：清洗/统计/图表/执行/查询各环节均产出且与任务一致（15 分）。
- [ ] 结果正确性：无"缺失当 0"、分组前已去重、t 检验前提已查、SQL WHERE 完整、单位与有效数字一致（15 分）。
- [ ] 结果完整性：output_contract 全字段回填，主产物 + 预览齐备（5 分）。
- [ ] 可核验性：原始快照、规则表、配方、依赖版本、种子、命令行、日志、退出码留痕；sources 每项带 locator + 逐字 quote（5 分）。
- [ ] 增益可验证：日志能证明调试轮次减少、一次跑通、复核成本下降（不靠文字自述）（增益项）。
- [ ] 新增能力/方法改进：字段对齐、口径显式化、缺失/异常策略可解释、配方+中间表示的确定性重跑优于裸跑（15 分）。
- [ ] 调用与易用性：按 §4 示例独立完成"给数据、出结果"（3 分）。
- [ ] 运行稳定性：空输入、坏 JSON、坏编码、缺参数四种场景退出码 0 且产出合法结果（4 分）。
- [ ] 异常处理：缺列、类型不符、依赖缺失、查询为空、数值溢出、权限不足均有识别/重试/降级/转人工策略（3 分）。
- [ ] 生态复用：脚本、schema、失败处理说明齐全，他人可直接复用复跑（10 分）。

提交前运行自检：`python3 -B scripts/selftest.py`（验证 frontmatter、脚本 `--help`、引擎 SVG/PNG、空输入端到端）。

## 6. 降级与异常策略
- 缺数据/材料不足：`status=insufficient_evidence`，sources 列出已读证据，uncertainty 列缺口，标 `pending_human`；不编造。
- 缺软件/依赖（scientific-python/SQLite 不可用）：引擎全程标准库（`statistics`/`sqlite3`/自绘 SVG+PNG），统计与绘图不依赖第三方包；在 change_log 及 `degradations`/`limitations` 如实标注，不用假数据补。
- 坏 JSON/坏编码：捕获记入 errors/degradations，继续处理其余材料，不崩溃（编码按 utf-8-sig→utf-8→gbk→latin-1 容错）。
- 缺列/类型不符：记 errors，尝试字段别名匹配；仍失败转 `pending_human`。
- 查询为空/数值溢出：如实记 `not_reported` 或空集，附 SQL 原文与库路径，不静默跳过。
- 写盘失败：降级写最小合法 result.json（case_id + status + errors），退出码仍 0。

## 7. 边界
- 只做给定数据分析链路，不扩展到实验设计、机理解释、论文写作。
- 不提交预制答案、白板或测试案例；不声称任何 benchmark 分数或"已测增益"。
- 正式评分只由平台 3 个固定案例对照产生；本 Skill 只负责让 Agent 在该流程上稳定产出可复核结果。差异化架构：**声明式配方 + 单一中间表示 state.json + 先校验后交付 + 纯标准库 SVG/PNG 渲染**，与线性 init→profile→任意脚本→validate 的 CLI 链明显不同。
