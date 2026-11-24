---
name: search-coverage-evidence
description: 科研文献检索覆盖率与遗漏分析（证据溯源强化版）。从给定材料构建可独立复核的冻结参考集，逐条判定命中/漏检并计算覆盖率，把每条漏检归因到具体查询式/检索字段/来源库/时间窗，评估补充检索的边际收益；每条结论均附 locator+逐字 quote 的 evidence_map。产出可复算的 result.json、命中矩阵与覆盖率报告。当任务涉及文献检索覆盖率、漏检归因或补充检索增益时使用。
version: 1.0.0
---

# 检索覆盖率与遗漏分析（证据溯源强化版）

## 1. 角色与何时使用

给定研究问题、检索材料与参考集，交付"算覆盖—列漏检—做补检"的**可核对、可复算**闭环。核心判据三条：覆盖率分子分母是否来自可核对的参考集；每条漏检能否指认是哪一次查询/字段/来源/时间窗造成；补检后遗漏是否真的下降、残余遗漏是否有解释。

适用：有独立参考集可据的科研文献检索任务，要求报告哪些关键文献被找到/漏掉、遗漏发生在哪、补检是否减少遗漏。

不适用：无参考集可据的一次性检索；要求实时接入在线数据库（本题禁止伪造线上覆盖声明）；系统综述正式纳排流程。

四种失效方式必须主动规避：①以检索结果反推应检文献（分母必须来自独立参考集）；②把"没找到"当"不相关"直接划掉（漏检必须保留并统计）；③归因只写"数据库覆盖不足/关键词不好"（必须落到具体查询式、字段、来源、时间窗）；④补检只换同义词重跑而命中集合无新增，却声称遗漏下降。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（案例标识、任务约束、可能的 `output_contract`）、`task.md`（题目要求）、以及组织方提供的材料/文献/查询响应文件（PDF、Crossref/EuropePMC/publisher 响应、参考图等，路径与命名不定）。
- 不假设固定文件名/键名：先用 `python scripts/main.py inspect` 盘点，再按内容识别参考集与检索结果；PDF 用预装 `pdf-text-tool` 抽文本侧车到 `/app/work/<name>.txt` 后由脚本读取，不按标签臆测内容。
- 文本一律 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级到 `errors`/`degradations`，不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- 若 `case.json` 声明 `output_contract`，其全部键必须**原样回填**（缺值 `null`/`[]`，不改名、不删未知键）。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON），至少包含：

- `case_id`（取自 case.json 的 case_id/id/challenge_id，取不到用内容哈希，不虚构）
- 题目 `output_contract` 的全部字段（若有），原样回填
- `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实标注
- 覆盖闭环核心字段：`research_question`、`search_scope`、`reference_set`（冻结版本+逐条记录）、`search_sessions`、`coverage`（denominator/numerator/coverage_rate/omission_rate/hit_rule）、`hit_matrix`（逐条命中状态+匹配依据+证据）、`omissions`（逐条漏检+归因+证据）、`supplementary_searches`、`marginal_gain`、`residual_omissions`
- 溯源与诚实字段：`evidence_map`（每项 locator+逐字 quote+verbatim+status）、`sources`、`uncertainty`、`change_log`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`

可编辑副产物（便于复核）：`/app/output/coverage_report.md`、`/app/output/hit_matrix.csv`、`/app/output/evidence_map.json`。主结果写入前先落骨架，任何中断都不留半截 JSON。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

时间纪律：**前 3 分钟必须落盘一版合法 result.json 骨架**，此后只做增量改进；每次写盘前 `change_log` 记一条。禁止在骨架未落盘前做长链分析。

1. **scaffold（0–3 min）**：`python scripts/main.py scaffold --input /input --output /app/output --work /app/work`。输出：`result.json` 骨架（status=partial，全部契约键回填 null/[]）。若连骨架都写不出，用最小 JSON 手工落盘并标 input_error。
2. **inspect（3–6 min）**：`python scripts/main.py inspect --input /input --output /app/output --work /app/work`。输出：`/app/work/inspect.json`（文件清单、候选记录数、解析错误）。据此确定"哪个文件是参考集、哪些是检索结果/查询响应"；PDF 先经 `pdf-text-tool` 抽文本侧车。
3. **build+match（6–10 min）**：`python scripts/main.py run --input /input --output /app/output --work /app/work [--refs <文件或glob> --results <文件或glob>]`。输出：冻结 `reference_set.json`、`hit_matrix.csv`、`coverage_core.json`，并把覆盖率/命中矩阵/漏检初判合并进 result.json。命中规则：标识符精确匹配 → 标题+第一作者+年份模糊匹配(0.88) → 转人工裁决(0.70–0.88)。
4. **复核与补检（10–13 min）**：读 `/app/work/coverage_core.json`，对 `adjudicate`/`miss` 记录逐条裁决与归因；依据漏检类别设计新查询式并写 `--updates` 文件（补检只统计材料中真实存在、且原先未命中的新增记录，禁止虚构命中）。同一来源二次确认仍缺证据的，标 `pending_human`。
5. **merge+finalize（13–15 min）**：`python scripts/main.py merge --output /app/output --work /app/work --updates <更新文件>`；随后写 `coverage_report.md`、`evidence_map.json`，跑第 5 节自检，更新 status/uncertainty/change_log。

预算保护：每步用 1 次请求跑脚本；把裁决/归因合并到单个 updates 文件再 merge，避免逐条往返消耗请求；剩余请求用于对关键漏检的归因论证（补逐字证据），而非重复读同一文件。

## 5. 自检清单（交付前逐条勾选，对应评分维度）

实际效果：□ 分母来自独立冻结参考集而非检索结果自身；□ coverage_rate=命中数/参考集规模，分子分母可复算；□ hit_matrix 逐条有状态+匹配依据+证据；□ 每条漏检有归因类别+证据，不写"数据库覆盖不足"这类空话。

使用增益：□ 同一参考集与查询预算下，补检前后命中集合对比真实（新增命中来自材料中真实记录）；□ 报告 marginal_gain 与残余遗漏，而非只报"遗漏下降"；□ 逐条比对/去重由脚本完成，人工只做裁决与归因。

创新增益：□ 参考集对齐、漏检分类、补检触发规则可复用（见 `references/coverage-method.md`）；□ 覆盖率口径与阈值有文字定义；□ 说明本方法相对"以结果定义分母"的边界。

可靠体验：□ `python scripts/main.py --help` 退出码 0，四个子命令可列出；□ 空目录/坏 JSON/缺参考集三态各跑一遍均退出码 0 且 result.json 合法；□ 重复运行同一输入结果一致（无随机性）；□ 撤稿/重复记录/无标识/标识冲突/来源超时均被识别并标注。

生态贡献：□ 参考集来源、覆盖率口径、漏检分类、补检流程、限制齐全；□ 脚本与模板可迁移到新主题；□ 报告范式沉淀到 `coverage_report.md` 模板。

**本版强化点（证据溯源）**：每条结论/数值/图元必须能指回材料的逐字片段（locator + quote）；`evidence_map` 每条含 `{evidence_id, claim, locator, quote, verbatim, status}`，且命中与漏检各有独立证据条目；缺失证据写 `not_reported`，绝不补造。相较"仅写来源文件"的弱溯源，本版把证据定位到文件内字节/JSON 路径并逐字引用，使复核者可不依赖脚本独立复算。

## 6. 降级策略（禁止编造）

- 无参考集或材料不足 → status=`insufficient_evidence`，列出缺口，不编文献、不编命中、不编覆盖率。
- 坏 JSON/坏编码 → 记录到 `errors`/`degradations`，继续处理其余材料。
- 缺软件/缺网络 → 用本地材料与标准库脚本完成任务，把无法完成的实时校验写入 `limitations`/`pending_human`。
- 标识冲突/撤稿/全文不可得 → 不猜归属；冲突记录并存并标 `source_conflict`，撤稿标 `retracted_or_unavailable`。
- 每条结论/数值/图元必须能指回材料的逐字片段（locator+quote）；缺失就写 `not_reported`，绝不补造 DOI、年份或检索结果。

## 7. 脚本调用约定

- 全部脚本在 `scripts/` 下，仅依赖 Python 标准库，用相对路径调用（如 `python scripts/main.py ...`），无需安装。
- 子命令：`scaffold` / `inspect` / `run` / `merge`；`--input/--output/--work` 缺省分别指向 `/input`、`/app/output`、`/app/work`。
- 方法与阈值详见 `references/coverage-method.md`；详细核对清单见 `references/checklist.md`。
- 自测：`python scripts/selftest.py`（覆盖 help、空目录、坏 JSON、正常样例、重复运行一致性；正常样例仅作自检，非正式案例结果）。

**最小调用示例（示例，非正式案例结果）**：输入 `/input` 含 `case.json`、`refs.json`（参考集）、`crossref-results.json`（检索结果）；依次执行 `scaffold → inspect → run`，再对 `adjudicate`/`miss` 记录人工写 `updates.json` 后 `merge`；最终 `/app/output/result.json` 含 case_id、coverage_rate、hit_matrix、omissions、marginal_gain 与 evidence_map。
