---
name: search-coverage-analysis
description: 科研文献检索覆盖率与遗漏分析。从给定材料构建可独立核对的冻结参考集，逐条判定命中/漏检并计算覆盖率，把每条漏检归因到具体查询式/字段/来源库/时间窗，评估补充检索的边际收益与残余遗漏，产出可复算的 result.json、命中矩阵与覆盖率报告。当任务涉及检索覆盖率、漏检归因或补充检索增益时使用。
version: 1.1.0
---

# 检索覆盖率与遗漏分析

## 1. 角色与何时使用

本 Skill 完成"算覆盖—列漏检—做补检"的可核对闭环：给定研究问题、检索材料与独立参考集，交付覆盖率、逐条命中状态、漏检归因和补检边际收益。适用条件：任务要求报告"哪些已知文献被找到/漏掉、遗漏发生在哪条查询/字段/来源、补检是否真的减少遗漏"。

不适用：无参考集可据的一次性检索；要求实时接入在线数据库（本题禁止编造线上覆盖声明）；系统综述的正式纳排流程。

四种失效必须规避：①以检索结果反推应检文献（分母必须来自独立冻结参考集）；②把"没找到"当"不相关"直接划掉（漏检必须保留并统计）；③归因只写"数据库覆盖不足"这类空话（必须落到具体查询式/字段/来源/时间窗）；④补检只换同义词重跑而命中集无新增却声称遗漏下降。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（案例标识、任务约束、可能的 `output_contract`）、`task.md`（题目要求）、以及组织方提供的材料/文献/查询响应文件（PDF、Crossref/EuropePMC/publisher 响应等，路径与命名不定）。
- 不假设固定文件名/键名：先 `scripts/analyze.py inspect` 盘点，再按内容识别参考集与检索结果；PDF 用环境提供的解析工具读原文，不按标签臆测内容。
- 文本一律 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级到 `errors`/`degradations`，不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- 若 `case.json` 声明 `output_contract`，其中的全部键必须**原样回填**（缺值 `null`/`[]`，不改名、不删未知键）。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON），至少包含：`case_id`（取自 case.json 的 case_id/id/challenge_id，取不到用内容哈希，不虚构）；`output_contract` 的全部字段（若有，原样回填）；`status` ∈ `ok | partial | insufficient_evidence | input_error`；`research_question`、`search_scope`、`reference_set`（冻结版本+逐条记录+排除记录）、`search_sessions`、`coverage`（denominator/numerator/coverage_rate/omission_rate/hit_rule）、`hit_matrix`、`omissions`、`supplementary_searches`、`marginal_gain`、`residual_omissions`；溯源字段 `evidence_map`（每项 locator+逐字 quote）、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`limitations`、`runtime`。

可编辑副产物（便于复核）：`/app/output/coverage_report.md`、`/app/output/hit_matrix.csv`、`/app/output/evidence_map.json`。主结果先落骨架再写全量，任何中断都不留半截 JSON。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

**时间纪律：前 3 分钟必须落盘一版合法 result.json 骨架**，此后只做增量改进；每次写盘前 `change_log` 记一条。禁止在骨架未落盘前做长链分析。

1. **scaffold（0–3 min）**：`python scripts/analyze.py scaffold --input /input --output /app/output --work /app/work`。输出 `result.json` 骨架（status=partial，全部契约键回填 null/[]）。连骨架都写不出时，手工落盘最小 JSON 并标 `input_error`。
2. **inspect（3–6 min）**：`python scripts/analyze.py inspect`。输出 `/app/work/inspect.json`（文件清单、候选记录数、解析错误）。据此确定"哪个文件是参考集、哪些是检索结果/查询响应"。
3. **build+match（6–10 min）**：`python scripts/analyze.py run [--refs <文件或glob> --results <文件或glob>]`。输出冻结 `reference_set.json`、`hit_matrix.csv`、`coverage_core.json`，并把覆盖率/命中矩阵/漏检初判合并进 result.json。命中规则：标识符精确匹配 → 标题+第一作者+年份模糊匹配 → 转人工裁决（阈值见 references/method.md）。
4. **复核与补检（10–13 min）**：读 `/app/work/coverage_core.json`，对 `adjudicate`/`miss` 记录逐条裁决与归因；依据漏检类别设计新查询式，写 `--updates` 文件（补检只统计材料中真实存在且原先未命中的新增记录，禁止虚构命中）。同一来源二次确认仍缺证据的标 `pending_human`。
5. **merge+finalize（13–15 min）**：`python scripts/analyze.py merge --updates <更新文件>`；随后写 `coverage_report.md`、`evidence_map.json`，跑第 5 节自检，更新 status/uncertainty/change_log。

预算保护：每步用 1 次请求跑脚本；把裁决/归因合并到单个 updates 文件再 merge，避免逐条往返；剩余请求用于关键漏检的归因论证，而非重复读同一文件。

## 5. 自检清单（交付前逐条勾选，对应评分维度）

实际效果：□ 分母来自独立冻结参考集而非检索结果自身；□ coverage_rate=命中数/合格参考集规模，分子分母可复算；□ hit_matrix 逐条有状态+匹配依据；□ 每条漏检有归因类别+证据，不写空话；□ 冲突信息并存并标 `source_conflict`；□ 撤稿/无法获取全文/无标识/标识冲突逐类标注。

使用增益：□ 补检前后命中集对比真实，新增命中来自材料真实记录；□ 报告 marginal_gain 与残余遗漏，而非只报"遗漏下降"；□ 逐条比对/去重由脚本完成，人工只做裁决与归因。

创新增益：□ 参考集对齐、漏检分类、补检触发/停止规则可复用（见 references/method.md）；□ 覆盖率口径与阈值有文字定义；□ 说明本方法相对"以结果定义分母"的边界。

可靠体验：□ `python scripts/analyze.py --help` 退出码 0；□ 空目录/坏 JSON/缺参考集三态各跑一遍均退出码 0 且 result.json 合法；□ 重复运行同一输入关键数值一致（无随机性）；□ 单文件 >2MB 被跳过并记录。

生态贡献：□ 参考集来源、覆盖率口径、漏检分类、补检流程、限制齐全；□ 脚本与模板可迁移到新主题；□ 报告范式沉淀到 coverage_report.md 模板；□ 最小调用示例标明为示例，不冒充正式案例。

## 6. 降级策略（禁止编造）

- 无参考集或材料不足 → status=`insufficient_evidence`，列出缺口，不编文献、不编命中、不编覆盖率。
- 坏 JSON/坏编码 → 记录到 `errors`/`degradations`，继续处理其余材料。
- 缺软件/缺网络 → 用本地材料与标准库脚本完成任务，把无法完成的实时校验写入 `limitations`/`pending_human`。
- 标识冲突/撤稿/全文不可得 → 不猜归属；冲突记录并存并标 `source_conflict`，撤稿标 `retracted_or_unavailable`。
- 每条结论/数值必须能指回材料的逐字片段（locator+quote）；缺失写 `not_reported`，绝不补造 DOI、年份或检索结果。

## 7. 脚本调用约定

- 全部脚本在 `scripts/` 下，仅依赖 Python 标准库，用相对路径调用（`python scripts/analyze.py …`），无需安装。
- `--input/--output/--work` 缺省分别指向 `/input`、`/app/output`、`/app/work`；本地自测可传绝对路径覆盖。
- 方法与阈值详见 `references/method.md`；自测：`python scripts/selftest.py`（空目录、坏 JSON、正常样例、确定性各跑一遍）。
