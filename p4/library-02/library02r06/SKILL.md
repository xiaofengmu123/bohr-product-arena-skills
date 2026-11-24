---
name: library-document-retrieval
description: 科研资料库智能检索与修复（scientific document library retrieval & repair）：只读盘点文件、目录、权限与符号/硬链接别名，按内容 SHA-256 去重并按归一化主干+内容相似度重建候选版本链，解析库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite）并恢复对账（matches/mismatches/missing/orphaned），构建词元+CJK 二元组可检索索引并回答常见检索问题，产出可复核的分层中间表示（plan.json / inventory.json / library_model.json / evidence_model.json）与 result.json。当需要整理科研资料库、维护元数据与版本关系、修复缺失/断链/重复、执行过滤条件并给出证据回链时使用。
version: 1.0.0
---

# 科研资料库智能检索与修复 Skill

## 1. 角色与何时使用

面向"文件已经在库里，如何被准确找到、定位到章节、按条件筛出并给出可回链证据"的任务：来源混杂、命名不一、存在副本/修订版/别名/断链与不一致元数据的科研资料库。核心价值是**动手落库 + 留证据**，而非只给建议。

适用：文件盘点、目录归位、按内容去重、版本链重建、别名识别、库目录解析与对账、检索问答、可交接清单与操作记录。不适用：开放网络文献检索、论文全文精读、一次性脚本、纯聊天问答。

## 2. 分层中间表示（本版核心设计，区别于"一次性出结果"）

脚本把结果拆成四层**结构化中间产物**，每层带 `schema` 版本号，后层由前层机械推导，`--check` 可逐层回验：

| 层 | 产物 | 语义 |
|-|-|-|
| 任务层 | `plan.json` | goals / rules / filter_dimensions / questions / output_contract（从 case.json+task.md 解析） |
| 数据层 | `inventory.json` | 文件、目录、权限、符号/硬链接、SHA-256、文本提取状态 |
| 领域层 | `library_model.json` | 去重组、版本候选、别名、对账、检索索引 |
| 证据层 | `evidence_model.json` | 每条结论 → locator + 逐字 quote + verified |

`result.json` 是这四层的**汇总视图**（含 contract 全量回填 + sources/uncertainty/change_log）。设计取舍：确定性计算与语义判断分层，Agent 只在脚本给出的候选上做语义确认，避免把猜测混进机械证据。

## 3. 输入契约（运行时，只读）

- `/input/case.json`：案例标识、任务说明与 `output_contract`（键名可能是 `output_contract`/`output`/`result_schema`/`contract` 等，脚本自动识别）。
- `/input/task.md`：题目规则、字段要求、行政版本说明与完成标准。
- `/input/` 其余文件：科研材料。**以真实文件内容为准，不依据领域标签补造数据。**

工作目录 `/app/work/`（中间提取文本）；最终产物 `/app/output/`。硬约束：源文件只读，不移动、不覆盖、不删除；同一输入重复运行结果一致（遍历排序、哈希稳定、无随机数；仅时间戳为运行时差异）。

## 4. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`：从 case.json/task.md 识别。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注。
- 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`，不改名、不删未知键；与保留键冲突时改存 `computed._contract_<字段>`）。
- `sources`：关键结论的证据来源（locator + 逐字 quote）。
- `uncertainty`：不确定项、原因与严重度。
- `change_log`：操作时间线（time/actor/step/detail）。
- 辅助字段：`errors`、`degradations`、`runtime`、`limitations`、`pending_human`；机械证据统一放 `computed`（plan/files/summary/duplicate_groups/version_candidates/aliases/reconciliation/index_terms/answers/artifacts/evidence）。

其余可编辑产物（均写入 `/app/output/`）：`plan.json`、`inventory.json`、`library_model.json`、`evidence_model.json`、`library_manifest.csv`、`directory_tree.txt`、`operation_log.jsonl`。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求，每响应 ≤16384 token）

原则：**先落盘、再分析、后增量改进**；任一阶段失败都不得留下空产物。

| 步骤 | 输入 | 动作与输出 | 时间预算 |
|-|-|-|-|
| 0 骨架 | case.json + task.md | 立即执行 `python3 -B scripts/main.py --skeleton-only`，写出含 `case_id` + contract 占位 + `status=partial` 的 result.json 与 plan.json；同时读 task.md 提取规则、字段与版本说明 | ≤3 分钟（必须完成落盘） |
| 1 盘点建模 | /input/ 全目录 | 执行 `python3 -B scripts/main.py`，产出四层 IR + 全部可编辑产物 + 完整 result.json | ≤2 分钟 |
| 2 语义归位 | 清单 + 规则 | 按题目规则确认目录组织、去重判定、版本链、元数据字段与问答；把语义结论写成 patch，用 `python3 -B scripts/main.py --update patch.json --result /app/output/result.json` 增量合并 | 剩余约 6–8 分钟 |
| 3 证据复核 | result.json + 产物 | 为每个关键结论补 `sources`（locator+quote）；执行 `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` | ≤2 分钟 |
| 4 定稿 | 复核通过 | 将 `status` 改为 `ok`，或按实际缺口保留 `partial`/`insufficient_evidence`；确保所有 contract 字段已填 | 收尾 |

模型请求纪律：脚本读写不消耗模型请求；64 次请求优先用于读题、读材料、语义判定与复核。每步完成即 `--update` 落盘，避免最后一次性写结果丢失进度。

## 6. 证据溯源（硬要求）

- 每个关键结论/数值/重复或版本判定，都要有 `sources` 条目：`{ "item": ..., "locator": "materials/xxx.txt#L12", "quote": "逐字原文" }`。
- 冲突不隐藏：同一指标两个来源不一致时都记录并标 `source_conflict`。
- 不虚构引用、DOI、哈希、数值；缺失写 `not_reported` 或放入 `pending_human`。
- `--check` 对文本文件的 quote 做逐字子串校验、对全部材料重算 SHA-256，保证可复核。

## 7. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] **实际效果**：已产出真实 plan/inventory/library_model/evidence_model 与 manifest/目录树/操作日志，而非仅建议；哈希与源文件一一对应。
- [ ] **实际效果**：覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源；难处理文件未静默跳过（写入 errors/degradations）。
- [ ] **可核验**：`python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` 退出码 0，quote 逐字校验通过、哈希重算一致。
- [ ] **使用增益**：去重按内容哈希而非文件名；版本按归一化主干 + 内容差异给候选并标注启发式；库目录自动解析并对账。
- [ ] **创新增益**：四层中间表示 + `--check` 跨层回验；别名（符号链接含断链 + 硬链接组）、对账（matches/mismatches/missing/orphaned）、词元+CJK 二元组索引均已落盘。
- [ ] **可靠体验**：空目录、坏 JSON、坏编码、缺权限、超大文件均不崩溃并记录；`result.json` 通过 `--check`；`status` 诚实、`case_id` 非空、contract 字段齐全。
- [ ] **文档**：SKILL.md、脚本与 references/ 路径有效；调用示例已标明"示例"。

## 8. 降级策略

- **缺数据/缺软件**：如实写 `status=insufficient_evidence` 或 `partial`，在 `errors`/`degradations`/`uncertainty` 说明，**禁止编造**文件内容、哈希、元数据或运行结果。
- **输出契约缺失**：脚本仍产出仅含元数据字段的合法 result.json，并在 `uncertainty` 标 `output_contract` 缺失。
- **超大/符号链接/不可读/非常规文件**：跳过内容哈希，保留路径、大小、权限、链接目标，记录 `too_large`/`symlink`/`unreadable`/`non_regular`。
- **SQLite 打不开/坏表、坏 JSON、坏编码**：记录 `sqlite_open_error`/`bad_json`/`bad_encoding`，不崩溃，继续处理其余材料。
- **写盘失败**：保留已落盘骨架，退出码 2，不崩溃；重启后可用 `--update` 续跑。

## 9. 边界

只解决"盘点 + 版本 + 别名 + 对账 + 索引 + 检索问答 + 证据回链"这一类任务；不做通用文件系统、不做内容级语义理解（由 Agent 在脚本候选上补充）；没有真实材料时不生成示例答案。

## 最小调用示例（示例，非正式案例结果）

```bash
python3 -B scripts/main.py --case-json /input/case.json --task-path /input/task.md \
  --input-root /input --output-dir /app/output --work-dir /app/work
# 示意输出: finalize: 12 files, 2 dup groups, 1 version candidates, 1 aliases, 0 orphaned -> /app/output/result.json
```
