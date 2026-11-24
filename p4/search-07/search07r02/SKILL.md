---
name: search-coverage-timeboxed
description: 科研文献检索覆盖率与遗漏分析的时限型工作流。前 3 分钟先落盘可复核的 partial 结果骨架，再按阶段预算增量构建独立冻结参考集、逐条命中判定、漏检归因与补检边际收益评估，全程原子写盘、证据逐字溯源、确定性复算，产出 result.json、命中矩阵与覆盖率报告。当任务涉及检索覆盖率、漏检归因或补充检索增益时使用。
version: 2.0.0
---

# 检索覆盖率与遗漏分析（时限型）

## 1. 角色与何时使用

本 Skill 用于"科研文献检索"场景：给定研究问题、检索材料与独立参考集，交付**可核对、可复算**的"算覆盖—列漏检—做补检"闭环。适用条件：任务要求报告哪些关键文献被找到/漏掉、遗漏发生在哪条查询/字段/来源/时间窗、补检是否真正减少遗漏。

不适用：无独立参考集可据的一次性检索；要求实时接入在线数据库（本题禁止伪造线上覆盖声明）；系统综述的正式纳排流程。

必须主动规避四种失效方式：①以检索结果反推应检文献（分母必须来自独立冻结参考集）；②把"没找到"当"不相关"划掉（漏检必须保留并统计）；③归因只写"数据库覆盖不足/关键词不好"（必须落到具体查询式、字段、来源、时间窗）；④补检只换同义词重跑而命中集合无新增，却声称遗漏下降。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（案例标识、任务约束、可能的 `output_contract`）、`task.md`（题目要求）、以及组织方提供的材料/文献/查询响应文件（PDF、Crossref/EuropePMC/publisher 响应、参考图等，路径与命名不定）。
- **先发现、再假设**：不假设固定文件名/键名，用 `scripts/main.py inspect` 盘点，再按内容识别参考集与检索结果；PDF 用现有 `pdftotext` 读原文，不按标签臆测内容。
- 文本一律 `utf-8-sig` 优先并多编码降级（BOM、坏编码、坏 JSON 降级到 `errors`/`degradations`，不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- 若 `case.json` 声明 `output_contract`，其全部键必须**原样回填**（缺值 `null`/`[]`，不改名、不删未知键，完整存于 `output_contract` 并扁平化到顶层）。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON），至少包含：

- `case_id`（取自 case.json 的 case_id/id/challenge_id，取不到用内容哈希，不虚构）
- 题目 `output_contract` 的全部字段（若有），原样回填
- `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实标注
- 覆盖闭环：`research_question`、`search_scope`、`reference_set`（冻结版本+逐条记录）、`search_sessions`、`coverage`（denominator/numerator/coverage_rate/omission_rate/hit_rule）、`hit_matrix`（逐条状态+匹配依据）、`omissions`（逐条漏检+归因+证据）、`supplementary_searches`、`marginal_gain`、`residual_omissions`
- 溯源与诚实：`evidence_map`（每项 locator+逐字 quote）、`sources`、`uncertainty`、`change_log`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`

可编辑副产物：`/app/output/coverage_report.md`、`/app/output/hit_matrix.csv`、`/app/output/evidence_map.json`。主结果每次**原子写盘**（先写 `.tmp` 再 `os.replace`），任何中断都不留半截 JSON。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

**时间纪律：前 3 分钟必须先落盘一版合法 result.json 骨架，此后只做增量改进。** `scripts/main.py` 通过 `/app/work/state.json` 跨命令持久化墙钟预算，`runtime` 块记录每阶段耗时与预算剩余；超 900 秒标 `time_budget_exceeded` 但不中断。

| 阶段 | 时间窗 | 预算 | 命令 | 输入 → 输出 |
|---|---|---|---|---|
| scaffold | 0–3 min | 180s / 2 req | `python scripts/main.py scaffold` | `/input` → `/app/output/result.json` 骨架（status=partial，契约键 null/[]）、`/app/work/budget_plan.json` |
| inspect | 3–6 min | 180s / 6 req | `python scripts/main.py inspect` | `/input` → `/app/work/inspect.json`（文件清单、候选记录数、解析错误） |
| run | 6–10 min | 240s / 10 req | `python scripts/main.py run [--refs <glob> --results <glob>]` | 参考集与检索结果 → `reference_set.json`、`coverage_core.json`、`hit_matrix.csv`，合并进 result.json |
| adjudicate | 10–13 min | 180s / 30 req | 人工/Agent 读 `/app/work/coverage_core.json`，对 `adjudicate`/`miss` 逐条裁决与归因，写 `--updates` 文件 | 漏检归因与补检设计 |
| merge | 13–15 min | 120s / 10 req | `python scripts/main.py merge --updates <更新文件>` | 裁决/归因/补检 → 最终 result.json、`coverage_report.md`、`evidence_map.json`，跑第 5 节自检 |

预算保护：每阶段用 1 次请求跑脚本；把裁决/归因/补检合并到**单个 updates 文件**再 merge，避免逐条往返；剩余请求用于对关键漏检的归因论证，而非重复读同一文件。若骨架尚未落盘，禁止开始长链分析。

## 5. 自检清单（交付前逐条勾选，对应评分维度）

运行 `python scripts/selftest.py` 一次性覆盖以下检查；逐条勾选见 `references/checklist.md`。

- 效果：□ 分母来自独立冻结参考集；□ coverage_rate 分子分母可复算；□ hit_matrix 逐条有状态+匹配依据；□ 每条漏检有归因类别+证据。
- 增益：□ 补检前后命中集合对比真实；□ 报 marginal_gain 与残余遗漏；□ 逐条比对/去重由脚本完成。
- 创新：□ 参考集对齐、漏检分类、补检触发规则可复用；□ 覆盖率口径与阈值有文字定义；□ 说明相对"以结果定义分母"的边界。
- 可靠：□ `scripts/main.py --help` 退出码 0；□ 空目录/坏 JSON/缺参考集三态退出码 0 且 result.json 合法；□ 重复运行结果一致；□ 撤稿/重复/无标识/标识冲突/来源超时均被识别标注。
- 生态：□ 参考集来源、覆盖率口径、漏检分类、补检流程、限制齐全；□ 脚本与模板可迁移到新主题；□ 报告范式沉淀到 `coverage_report.md` 模板。

## 6. 降级策略（禁止编造）

- 无参考集或材料不足 → `status=insufficient_evidence`，列出缺口，不编文献、不编命中、不编覆盖率。
- 坏 JSON/坏编码 → 记录到 `errors`/`degradations`，继续处理其余材料。
- 缺软件/缺网络 → 用本地材料与标准库脚本完成任务，无法完成的实时校验写入 `limitations`/`pending_human`。
- 标识冲突/撤稿/全文不可得 → 不猜归属；冲突并存标 `source_conflict`，撤稿标 `retracted_or_unavailable`。
- 每条结论/数值/图元必须能指回材料的逐字片段（locator+quote）；缺失写 `not_reported`，绝不补造 DOI、年份或检索结果。

## 7. 脚本调用约定

- 全部脚本在 `scripts/` 下，仅依赖 Python 标准库，用相对路径调用（`python scripts/main.py ...`），无需安装。
- `--input/--output/--work` 缺省分别指向 `/input`、`/app/output`、`/app/work`；本地自测传绝对路径覆盖。
- 方法与阈值详见 `references/coverage-method.md`（title_fuzzy=0.88、title_adjudication=0.70、year_tolerance=1、命中规则、漏检分类、updates schema）；详细核对清单见 `references/checklist.md`。
- 自测：`python scripts/main.py --help`；随后 `python scripts/selftest.py`（空目录、坏 JSON、正常样例、确定性、契约回填各跑一次）。
