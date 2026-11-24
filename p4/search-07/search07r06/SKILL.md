---
name: search-coverage-analysis
description: 科研文献检索覆盖率与遗漏分析。把给定材料构建成可核对的冻结参考集与检索结果索引，逐条判定命中/漏检并计算覆盖率，把每条漏检归因到具体查询式/检索字段/来源库/时间窗，评估补充检索的边际收益；全部结论由结构化中间产物（plan.json→reference_set.json+search_index.json→match_model.json→attribution_model.json→result.json）派生并可回放复算。当任务涉及文献检索覆盖率、漏检归因或补充检索增益时使用。
version: 2.0.0
---

# 检索覆盖率与遗漏分析（中间表示可回放版）

## 1. 角色与何时使用

本 Skill 用于“科研文献检索”场景：给定研究问题、检索材料与参考集，交付可核对、可复算的“算覆盖—列漏检—做补检”闭环。核心不是写得多，而是**输入可解析、输出可复核、失败可解释、重复运行结果一致**。

适用条件：任务要求报告“哪些关键文献被找到/漏掉、遗漏发生在哪条查询/字段/来源/时间窗、补检是否真的减少遗漏”。

不适用：无参考集可据的一次性检索；要求实时接入在线数据库（本题禁止伪造线上覆盖声明）；系统综述的正式纳排流程。

四种失效方式必须主动规避：①以检索结果反推应检文献（分母必须来自独立参考集，先于匹配冻结）；②把“没找到”当“不相关”划掉（漏检必须保留并统计）；③归因只写“数据库覆盖不足/关键词不好”（必须落到具体查询式、字段、来源、时间窗）；④补检只换同义词重跑而命中集合无新增却声称遗漏下降。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（案例标识、任务约束、可能的 `output_contract`）、`task.md`（题目要求），以及组织方提供的材料/文献/查询响应文件（PDF、Crossref/EuropePMC/publisher 响应、参考图等，路径与命名不定）。
- 不假设固定文件名/键名：先 `python scripts/main.py inspect` 盘点，再按内容识别参考集与检索结果；PDF 用现有解析工具读原文，不按标签臆测内容。
- 文本按 `utf-8-sig -> utf-8 -> latin-1` 容错读取；单文件 >2MB 跳过并记录；坏 JSON/坏编码降级到 errors/degradations，不崩溃。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- 若 `case.json` 声明 `output_contract`，其全部键必须**原样回填**（缺值 `null`/`[]`，不改名、不删未知键）。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON），至少包含：

- `case_id`（取自 case.json 的 case_id/id/challenge_id，取不到用内容哈希，不虚构）
- 题目 `output_contract` 的全部字段（若有），原样回填
- `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实标注
- 闭环核心字段：`research_question`、`search_scope`、`reference_set`（冻结版本+逐条记录）、`search_sessions`、`coverage`（denominator/numerator/coverage_rate/omission_rate/hit_rule）、`hit_matrix`（逐条命中状态+匹配依据）、`omissions`（逐条漏检+归因+证据）、`supplementary_searches`、`marginal_gain`、`residual_omissions`
- 溯源与诚实字段：`evidence_map`（每项 locator+逐字 quote）、`sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`、`errors`、`degradations`、`runtime`
- **中间表示链**：`pipeline` 字段列出中间产物 DAG 与各文件 sha256，说明 result.json 如何被回放复算（本版核心差异）

可编辑副产物（便于复核）：`/app/output/coverage_report.md`、`/app/output/hit_matrix.csv`、`/app/output/evidence_map.json`。主结果写入前先落骨架，任何中断都不留半截 JSON。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

时间纪律：**前 3 分钟必须落盘一版合法 result.json 骨架**，此后只做增量改进；每次写盘前 `change_log` 记一条。禁止在骨架未落盘前做长链分析。

1. **scaffold（0–3 min）**：`python scripts/main.py scaffold --input /input --output /app/output --work /app/work`。输出 result.json 骨架（status=partial，全部契约键回填 null/[]）。连骨架都写不出就手工落最小 JSON 并标 input_error。
2. **inspect（3–6 min）**：`python scripts/main.py inspect ...`。输出 `/app/work/inspect.json`（文件清单、候选记录数、解析错误、参考集/检索结果文件名建议）。据此确定“哪个文件是参考集、哪些是检索结果”。
3. **plan（6–8 min）**：`python scripts/main.py plan --work /app/work [--plan-file decisions.json --freeze]`。把研究问题、检索范围、命中规则、漏检分类、补检触发与停止条件**先冻结**成 `plan.json`——这是“分母独立于检索结果”的决策留痕，先于 build/match。
4. **build+match（8–12 min）**：`python scripts/main.py build --input /input --work /app/work [--refs <文件/glob> --results <文件/glob>]`，再 `python scripts/main.py match --work /app/work --output /app/output`。产出冻结 `reference_set.json`、`search_index.json`、`match_model.json` 与 hit_matrix.csv。命中规则：标识符精确匹配 → 标题+第一作者+年份模糊匹配（阈值见 references/coverage-method.md）→ 人工裁决。
5. **复核与补检（12–14 min）**：读 `match_model.json`，对 adjudicate/miss 逐条裁决与归因，写一个 `--updates` 文件（adjudications + attributions + supplementary_searches），只统计材料中真实存在且原先未命中的新增记录，禁止虚构命中。同一来源二次确认仍缺证据标 pending_human。
6. **merge+finalize（14–15 min）**：`python scripts/main.py merge --output /app/output --work /app/work [--updates <更新文件>]`；随后 `python scripts/main.py report ...` 写报告与 evidence_map，跑第 5 节自检，更新 status/uncertainty/change_log。

预算保护：每步用 1 次请求跑脚本；把裁决/归因合并到单个 updates 文件再 merge，避免逐条往返消耗请求；剩余请求用于对关键漏检的归因论证，而非重复读同一文件。

## 5. 自检清单（交付前逐条勾选，对应评分维度）

实际效果：□ 分母来自独立冻结参考集而非检索结果自身；□ coverage_rate=命中数/参考集规模，分子分母可复算；□ hit_matrix 逐条有状态+匹配依据；□ 每条漏检有归因类别+证据，不写“数据库覆盖不足”空话。

使用增益：□ 补检前后命中集合对比真实（新增命中来自材料中真实记录）；□ 报告 marginal_gain 与残余遗漏；□ 逐条比对/去重由脚本完成，人工只做裁决与归因。

创新增益：□ 中间产物链（plan/reference_set/search_index/match_model/attribution_model）齐全且可回放；□ 覆盖率口径与阈值有文字定义；□ 说明“中间表示+merge 派生”相对“以结果定义分母/手写主结果”的边界。

可靠体验：□ `python scripts/main.py --help` 退出码 0；□ `python scripts/selftest.py` 全过；□ 空目录/坏 JSON/缺参考集三态均退出码 0 且 result.json 合法；□ 重复运行关键字段一致（无随机性）；□ 撤稿/重复/无标识/标识冲突/超大文件被识别标注。

生态贡献：□ 参考集来源、覆盖率口径、漏检分类、补检流程、限制齐全；□ 脚本与模板可迁移；□ 报告范式沉淀到 coverage_report.md 模板。

## 6. 降级策略（禁止编造）

- 无参考集或材料不足 → status=`insufficient_evidence`，列出缺口，不编文献、不编命中、不编覆盖率。
- 坏 JSON/坏编码 → 记录到 `errors`/`degradations`，继续处理其余材料。
- 缺软件/缺网络 → 用本地材料与标准库脚本完成任务，把无法完成的实时校验写入 `limitations`/`pending_human`。
- 标识冲突/撤稿/全文不可得 → 不猜归属；冲突并存并标 `source_conflict`，撤稿标 `retracted_or_unavailable`。
- 每条结论/数值必须能指回材料逐字片段（locator+quote）；缺失写 `not_reported`，绝不补造 DOI、年份或检索结果。

## 7. 脚本调用约定

- 全部脚本在 `scripts/` 下，仅依赖 Python 标准库，用相对路径调用（如 `scripts/main.py`），无需安装。
- `--input/--output/--work` 缺省分别指向 `/input`、`/app/output`、`/app/work`；本地自测可传绝对路径覆盖。
- 方法与阈值详见 `references/coverage-method.md`；逐条核对清单见 `references/checklist.md`。
- 自测：`python scripts/main.py --help`，然后 `python scripts/selftest.py`（样例为脚本内合成示例，不冒充正式案例结果）。
- 中间产物回放：result.json 永远由 `merge` 派生；复核者可据 `pipeline.intermediates` 的 sha256 校验中间产物未改动，并重跑 merge 复算覆盖率。
