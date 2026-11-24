---
name: library-document-repair
description: 科研资料库修复（document library repair）：只读盘点文件/目录/权限/符号与硬链接别名，按内容 SHA-256 去重，按归一化主干+内容相似度重建候选版本链，解析行政版本说明与库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite），恢复对账（matches/mismatches/missing/orphaned），重建可检索索引与机械问答，产出可复核的清单、目录树、去重版本记录、恢复报告、索引、交接报告与操作日志。当需要整理科研资料库、维护元数据与版本关系、修复缺失/断链/重复、或产出可交接清单时使用。
version: 1.0.0
---

# 科研资料库修复 Skill

## 1. 角色与何时使用

面向「document library repair」：来源混杂、命名不一、存在重复、多版本、别名、断链与不一致元数据的科研资料库，整理成可检索、可交接、可复跑的结构化结果。核心价值是**动手落库 + 留证据**：不只给分类建议，而是产出清单、哈希、目录树、别名、版本关系、恢复对账、索引与操作日志。

适用：文件入库组织、目录归位与去重、元数据抽取与溯源、版本链重建、缺失/断链恢复、检索问答、长期归档交接。不适用：一次性脚本、纯聊天问答、无需可复核证据的场景。

## 2. 输入契约（运行时，只读）

- `/input/case.json`：案例标识、任务说明与 `output_contract`（键名可能为 `output_contract`/`output`/`result_schema`/`contract` 等，脚本自动识别并原样回填）。
- `/input/task.md`：题目规则、组织约定、字段要求、行政版本说明与完成标准。
- `/input/` 其余文件：科研材料（文件、目录、库目录/清单、版本说明、权限信息等）。**以真实文件内容为准，不依据领域标签补造数据。**

工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`。硬约束：所有源文件只读，不移动、不覆盖、不删除源文件；同一输入重复运行结果一致（目录遍历排序、哈希稳定、无随机数、时间戳除外）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`：从 case.json 或 task.md 识别。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注。
- `contract` + 顶层回填：题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`，不改名、不删未知键；与保留键冲突时改存 `_contract_<字段>`）。
- `result`：主产物（summary/files/directory_tree/duplicate_groups/version_candidates/aliases/version_notes/reconciliation/index/answers）。
- `sources`：关键结论的证据来源（locator + 逐字 quote）。
- `uncertainty`：不确定项、原因与严重度。
- `pending_human`：需要人工裁决的缺口。
- `change_log`：操作时间线（time/actor/step/detail）。
- 辅助字段：`errors`、`degradations`、`limitations`、`runtime`、`computed`（计数摘要 + 产物路径）、`artifacts`（产物文件名→路径）。

其余可编辑产物（写入 `/app/output/`）：`library_manifest.csv`（文件清单）、`directory_tree.txt`（目录树，空目录标 `[空目录]`）、`dedup_version.json`（重复组、版本候选、版本说明、别名）、`recovery_report.json`（对账）、`library_index.json`（可检索索引）、`library_manifest.md`（交接报告）、`operation_log.jsonl`（操作日志）。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

原则：**先落盘、再分析、后增量改进**；任一阶段失败都不得留下空产物。每步完成后用 `--update` 落盘，避免最后一次性写结果丢失进度。

| 步骤 | 输入 | 动作与输出 | 时间预算 |
|---|---|---|---|
| 0 骨架 | case.json + task.md | 立即执行 `python3 -B scripts/main.py --skeleton-only`，写出含 `case_id` + contract 占位 + `status=partial` 的 result.json；同时读 task.md 提取组织规则、字段要求与版本说明 | ≤ 3 分钟（必须完成落盘） |
| 1 盘点与修复 | /input/ 全目录 | 执行 `python3 -B scripts/main.py`（内部：盘点→哈希→去重→版本→别名→版本说明→恢复对账→索引→问答→产物），产出全部可编辑产物并写完整 result.json | ≤ 2 分钟 |
| 2 语义归位 | 清单 + 规则 | 按题目规则确认目录组织、去重判定、版本链、元数据字段与问答；将语义结论写成 patch，用 `python3 -B scripts/main.py --update patch.json --result /app/output/result.json` 增量合并 | 剩余时间（约 6–8 分钟） |
| 3 证据与复核 | result.json + 产物 | 为每个关键结论补 `sources`（locator+quote）与 `uncertainty`；执行 `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` | ≤ 2 分钟 |
| 4 定稿 | 复核通过 | 将 `status` 改为 `ok`，或按实际缺口保留 `partial`/`insufficient_evidence`；确保所有 contract 字段已填 | 收尾 |

模型请求纪律：脚本读写不消耗模型请求；64 次请求优先用于读题、读材料、语义判定与复核。前 3 分钟先落 `partial` 骨架，之后只做增量改进。

## 5. 证据溯源（硬要求）

- 每个关键结论/数值/重复或版本判定，都要有 `sources` 条目：`{ "item": ..., "locator": "materials/xxx.pdf#p3" 或文件相对路径, "quote": "逐字原文" }`。
- 冲突不隐藏：同一指标两个来源不一致时，都记录并标 `source_conflict`。
- 不虚构引用、DOI、哈希、数值；缺失写 `not_reported` 或放入 `pending_human`。

## 6. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] **实际效果**：已产出真实清单/目录树/版本记录/恢复报告/索引，而非仅建议；落库文件与源文件一一对应，哈希一致。
- [ ] **实际效果**：覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六类要素；难处理文件未被静默跳过。
- [ ] **可核验**：`library_manifest.csv`、`operation_log.jsonl`、`dedup_version.json`、`recovery_report.json`、`library_index.json` 均存在，他人可按记录复跑并核对。
- [ ] **使用增益**：去重按内容哈希而非文件名；版本链按归一化主干 + 内容差异给候选，并标注为启发式。
- [ ] **创新增益**：别名识别（断链/硬链接）、行政版本说明解析、恢复对账与索引重建均已落盘。
- [ ] **可靠体验**：空目录、坏 JSON、坏编码、缺权限、超大文件均不崩溃，记录进 `errors`/`degradations`。
- [ ] **可靠体验**：`result.json` 通过 `--check`；`status` 诚实，`case_id` 非空，contract 字段齐全。
- [ ] **文档**：`SKILL.md`、脚本与 `references/` 路径有效；调用示例已标明"示例"，未冒充正式案例结果。

## 7. 降级策略

- **缺数据/缺软件**：如实写 `status=insufficient_evidence` 或 `partial`，在 `errors`/`degradations`/`uncertainty`/`limitations` 中说明，**禁止编造**文件内容、哈希、元数据或运行结果。
- **输出契约缺失**：脚本仍产出仅含元数据字段的合法 result.json，并在 `uncertainty` 标 `output_contract` 缺失。
- **超大文件/符号链接/不可读/非常规文件**：跳过内容哈希，保留路径、大小、权限、链接目标，记录 `too_large`/`symlink`/`unreadable`/`non_regular`。
- **SQLite 打不开/坏表**：记录 `sqlite_open_error`/`sqlite_query_error`，不崩溃。
- **写盘失败**：保留已落盘骨架，退出码 2，不崩溃；重启后可续跑 `--update`。

## 8. 边界

只解决「整理 + 证据 + 版本 + 别名 + 恢复对账 + 索引 + 检索问答」这一类任务；不做通用文件系统、不做内容级语义理解（由 Agent 在脚本给出的候选上补充）；没有真实材料时不生成示例答案。

## 最小调用示例（示例，非正式案例结果）

```bash
python3 -B scripts/main.py --case-json /input/case.json --task-path /input/task.md \
  --input /input --output /app/output --work /app/work
# 示意输出: finalize: 12 files, 2 dup groups, 1 version candidates, 1 broken symlinks, 0 reconcile mismatches -> /app/output/result.json
```
