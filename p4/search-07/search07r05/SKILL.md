---
name: search-coverage-contract
description: 科研文献检索覆盖率与遗漏分析。冻结独立参考集、逐条命中判定、把每条漏检归因到具体查询式/字段/来源/时间窗、评估补充检索边际收益，并产出字段与运行时输出契约逐一对齐、含 change_log 的 result.json、命中矩阵与覆盖率报告。当任务涉及文献检索覆盖率、漏检归因或补检增益时使用。
version: 1.1.0
---

# 检索覆盖率与遗漏分析（输出契约完整版）

## 1. 角色与何时使用

本 Skill 用于“科研文献检索”场景：给定研究问题、检索材料与参考集，交付一套**可核对、可复算、可审计**的“算覆盖—列漏检—做补检”闭环。核心不是写得长，而是**输入可解析、输出可复核、失败可解释、重复运行结果一致**。

适用条件：任务要求报告“哪些关键文献被找到/漏掉、遗漏发生在哪条查询/字段/来源/时间窗、补检是否真的减少遗漏”。不适用：无参考集可据的一次性检索；实时接入在线数据库；系统综述的正式纳排流程。

必须主动规避四种失效：①以检索结果反推应检文献（分母必须来自独立参考集）；②把“没找到”当“不相关”划掉（漏检必须保留并统计）；③归因只写“数据库覆盖不足”（必须落到具体查询式/字段/来源/时间窗）；④补检只换同义词重跑却报遗漏下降（`reduction` 只按真实新增命中计数）。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（案例标识、任务约束、可能的 `output_contract`）、`task.md`（题目要求），以及组织方提供的材料/文献/查询响应文件（PDF、Crossref/EuropePMC/publisher 响应、参考图等，路径与命名不定）。
- **先盘点、后假设**：`python scripts/main.py inspect` 列出文件清单、候选记录数与解析错误；按内容识别参考集与检索结果，不按文件名臆测。PDF 用平台预装解析工具读原文。
- 文本一律 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级到 `errors`/`degradations`，不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- 若 `case.json` 声明 `output_contract`，其中的全部键必须**逐字回填**（缺值 `null`/`[]`，不改名、不删未知键）；未声明时脚本写入由运行时契约推导的字段清单并标 `source=runtime_contract`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON），共 25 个顶层键（权威定义见 `references/contract.md`，脚本内以 `CONTRACT` 常量声明），至少含：

- `case_id`（取自 case.json 的 case_id/id/challenge_id/armiId，取不到用内容哈希，不虚构）
- `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实标注
- `output_contract`（题目契约逐字回填，或推导契约）
- 覆盖闭环：`research_question`、`search_scope`、`reference_set`、`search_sessions`、`coverage`、`hit_matrix`、`omissions`、`supplementary_searches`、`marginal_gain`、`residual_omissions`
- 运行时契约三区域：`search_results`（检索结果）、`evidence`（证据记录）、`status_report`（状态说明）
- 溯源与诚实：`evidence_map`（每项 locator+quote+conclusion）、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`limitations`、`runtime`

可编辑副产物（便于复核）：`/app/output/coverage_report.md`、`/app/output/hit_matrix.csv`、`/app/output/evidence_map.json`。主结果先落骨架、原子写盘（临时文件 + `os.replace`），任何中断都不留半截 JSON。

**本版侧重点（输出契约完整）**：契约以数据声明、`validate` 子命令逐键校验；每次写盘由 `commit_result()` 统一追加一条结构化的 `change_log`（revision 单调递增 + UTC 时间 + 动作 + 触及字段），保证变更记录与产物同步而非靠约定。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

时间纪律：**前 3 分钟必须落盘一版合法 result.json 骨架**，此后只做增量改进；每步尽量 1 次请求跑脚本，把裁决/归因合并到单个 updates 文件再 merge，避免逐条往返。

1. **scaffold（0–3 min）**：`python scripts/main.py scaffold --input /input --output /app/output --work /app/work`。输出：result.json 骨架（status=partial，全部契约键回填 null/[]，含首条 change_log）。若连骨架都写不出，手工落最小 JSON 并标 input_error。
2. **inspect（3–6 min）**：`python scripts/main.py inspect ...`。输出：`/app/work/inspect.json`（文件清单、候选记录数、解析错误、角色猜测）。据此确定“哪个文件是参考集、哪些是检索结果/查询响应”。
3. **run（6–10 min）**：`python scripts/main.py run ... [--refs <glob>] [--results <glob>]`。输出：冻结 `reference_set`、`coverage`、`hit_matrix`、`omissions` 初判，并写副产物。命中规则：标识符精确 → 标题+第一作者+年份模糊（ratio≥0.88、年份容差 1）→ 标题模糊裁决带（0.70–0.88）。
4. **复核与补检（10–13 min）**：读 `/app/output/result.json`，对 `adjudicate`/`miss` 逐条裁决与归因；按漏检类别设计新查询式并写 `--updates` 文件（新增命中只统计材料中真实存在、原先未命中的记录，禁止虚构）。同源二次确认仍缺证据的，标 `pending_human`。
5. **merge+finalize（13–15 min）**：`python scripts/main.py merge --output /app/output --work /app/work --updates <更新文件>`；随后核对 `coverage_report.md`/`evidence_map.json`，跑 `python scripts/main.py validate --output /app/output`，更新 status/uncertainty/change_log。

预算保护：空目录/坏 JSON/缺参考集三态各跑一遍不超预算；剩余请求用于对关键漏检的归因论证，而非重复读同一文件。

## 5. 自检清单（交付前逐条勾选，对应评分维度）

实际效果：□ 分母来自独立冻结参考集而非检索结果自身；□ coverage_rate=命中数/参考集规模，分子分母可复算；□ hit_matrix 逐条有状态+匹配依据；□ 每条漏检有归因类别+证据，不写“数据库覆盖不足”这类空话。

使用增益：□ 同一参考集与查询预算下，补检前后命中集合对比真实（新增命中来自材料中真实记录）；□ 报告 marginal_gain 与残余遗漏；□ 逐条比对/去重由脚本完成，人工只做裁决与归因。

创新增益：□ 输出契约以数据声明、`validate` 可校验；□ change_log 由 commit_result 统一维护，revision 单调；□ 参考集对齐、漏检分类、补检停止规则可复用（见 references/contract.md）。

可靠体验：□ `python scripts/main.py --help` 退出码 0；□ `python scripts/selftest.py` 打印 PASS；□ 空目录/坏 JSON/缺参考集三态各跑一遍均退出码 0 且 result.json 合法；□ 重复运行同一输入结果一致（无随机性）；□ 撤稿/重复记录/无标识/标识冲突/来源超时均被识别并标注。

生态贡献：□ 参考集来源、覆盖率口径、漏检分类、补检流程、限制齐全；□ 脚本与模板可迁移到新主题（仅标准库）；□ 报告范式沉淀到 coverage_report.md 模板。

完整逐条清单见 `references/checklist.md`。

## 6. 降级策略（禁止编造）

- 无参考集或材料不足 → `status=insufficient_evidence`，列出缺口，不编文献、不编命中、不编覆盖率。
- 坏 JSON/坏编码 → 记录到 `errors`/`degradations`，继续处理其余材料。
- 缺软件/缺网络 → 用本地材料与标准库脚本完成任务，把无法完成的实时校验写入 `limitations`/`pending_human`。
- 标识冲突/撤稿/全文不可得 → 不猜归属；冲突记录并存并标 `title_conflict_same_doi`，撤稿/不可得标 `retracted_or_unavailable`。
- 每条结论/数值/图元必须能指回材料的逐字片段（locator+quote）；缺失就写 `not_reported`，绝不补造 DOI、年份或检索结果。

## 7. 脚本调用约定

- 全部脚本在 `scripts/` 下，仅依赖 Python 标准库（Python ≥3.8），用相对路径调用，无需安装。
- `--input/--output/--work` 缺省分别指向 `/input`、`/app/output`、`/app/work`，可置于子命令之后；本地自测可传绝对路径覆盖。
- 方法与阈值详见 `references/contract.md`；逐条核对清单见 `references/checklist.md`。
- 自测：`python scripts/main.py --help`；随后 `python scripts/selftest.py`（空目录、坏 JSON、正常样例各跑一次）。

## 8. 最小调用示例（示例，非正式案例结果）

```bash
python scripts/main.py scaffold --input /input --output /app/output --work /app/work
python scripts/main.py inspect  --input /input --output /app/output --work /app/work
python scripts/main.py run      --input /input --output /app/output --work /app/work
python scripts/main.py merge    --output /app/output --work /app/work --updates /app/work/updates.json
python scripts/main.py validate --output /app/output
```

示意输出（标明为示例）：`result.json` 中 `status=partial`、`coverage={"denominator":10,"numerator":7,"coverage_rate":0.7,...}`、`change_log` 含 4 条记录。以上数值为示意，不冒充任何正式案例结果。
