---
name: scientific-library-retrieval
description: 科研资料库检索与修复（document library retrieval / repair）：只读盘点文件、目录、权限与符号/硬链接别名，按内容 SHA-256 去重并按归一化主干+内容相似度重建候选版本链，解析库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite）与行政版本说明，恢复对账（matches/mismatches/missing/orphaned），重建可检索索引并做章节级定位与证据回链，回答常见检索问题，产出可复核的清单、目录树、恢复报告、索引与操作日志。当需要整理科研资料库、维护元数据与版本关系、修复缺失/断链/重复、或按条件检索并给出可回链证据时使用。
version: 1.0.0
---

# 科研资料库检索与修复 Skill

## 1. 角色与何时使用

面向"科研文件智能检索"：来源混杂、命名不一、存在重复、多版本、别名、断链与不一致
元数据的科研资料库，整理成可检索、可交接、可复跑的结构化结果。核心价值是**动手落库 +
留证据**：不只给建议，而是产出清单、哈希、目录树、别名、版本关系、对账、索引、问答与
操作日志，并为每条结论给出 `locator + quote` 的可回链证据。

适用：文件入库组织、目录归位与去重、元数据抽取与溯源、版本链重建、缺失/断链恢复、
跨文件检索与章节定位、检索问答、长期归档交接。不适用：一次性脚本、纯聊天问答、无需
可复核证据的场景；不做开放网络文献检索与综述写作。

## 2. 输入契约（运行时，只读）

- `/input/case.json`：案例标识、任务说明与 `output_contract`（键名可能为
  `output_contract`/`output`/`result_schema`/`contract` 等，脚本自动识别）。
- `/input/task.md`：题目规则、组织约定、字段要求、行政版本说明与完成标准。
- `/input/` 其余文件：科研材料（文件、目录、库目录/清单、版本说明、问题文件等）。
  **以真实文件内容为准，不依据领域标签补造数据。**

工作目录 `/app/work/`（中间产物 `ledger.json`）；最终产物 `/app/output/`。硬约束：
所有源文件只读，不移动、不覆盖、不删除；同一输入重复运行结果一致。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`：从 case.json 或 task.md 识别。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注。
- 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`，不改名、不删未知键；
  与保留键冲突时改存 `_contract_<字段>`）。
- `sources`：关键结论证据来源（`item + locator + quote`）。
- `uncertainty`：不确定项、原因与严重度。
- `pending_human`、`change_log`、`errors`、`degradations`、`runtime`、`limitations`。
- `computed`：机械证据统一放这里（`files/summary/duplicate_groups/
  version_candidates/aliases/catalogs/reconciliation/answers/index/artifacts`）。

其余可编辑产物（写入 `/app/output/`）：`library_manifest.csv`（文件清单）、
`directory_tree.txt`（目录树，空目录标 `[空目录]`）、`dedup_version.json`（重复组、
版本候选、别名、版本说明）、`recovery_report.json`（对账）、`library_index.json`
（可检索索引）、`library_manifest.md`（交接报告）、`operation_log.jsonl`（操作日志）。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

原则：**先落盘、再分析、后增量改进**；任一阶段失败都不得留下空产物。脚本读写不消耗
模型请求；64 次请求优先用于读题、读材料、语义判定与复核。

| 步骤 | 输入 | 动作与输出 | 时间预算 |
|---|---|---|---|
| 0 骨架 | case.json + task.md | 立即执行 `python3 -B scripts/main.py --skeleton-only --input /input --output /app/output --work /app/work`，写出含 `case_id` + contract 占位 + `status=partial` 的 result.json；同时读 task.md 提取组织规则、字段要求与版本说明 | ≤ 3 分钟（必须完成落盘） |
| 1 机械落库 | /input 全目录 | 执行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`（内部：盘点→哈希→去重→别名→版本→库目录→对账→索引→问答→产物），写完整 result.json | ≤ 2 分钟 |
| 2 语义归位 | 清单 + 规则 + 账本 | 按题目规则确认目录组织、去重判定、版本链对齐、元数据字段与问答；将语义结论写成 patch，用 `python3 -B scripts/main.py --update patch.json --result /app/output/result.json` 增量合并 | 剩余时间（约 6–8 分钟） |
| 3 证据与复核 | result.json + 产物 | 为每个关键结论补 `sources`（locator+quote）与 `uncertainty`；执行 `python3 -B scripts/main.py --check --result /app/output/result.json --case-json /input/case.json` | ≤ 2 分钟 |
| 4 定稿 | 复核通过 | 将 `status` 改为 `ok`，或按实际缺口保留 `partial`/`insufficient_evidence`；确保所有 contract 字段已填 | 收尾 |

每步完成后用 `--update` 落盘，避免最后一次性写结果丢失进度。

## 5. 自检清单（逐条可勾选，对应评分维度）

见 `references/checklist.md`，并运行 `python3 -B scripts/selftest.py`（应 20 项全绿）。
关键项：实际效果（真实产物、SHA-256 去重、六类要素、难处理文件不静默跳过）、
使用增益（脚本做确定性层、Agent 做语义层）、创新增益（别名/证据账本/阶段边界校验）、
可靠体验（空目录/坏 JSON/坏编码/缺 case.json/重复运行均不崩溃）、生态贡献（文档齐全、
相对路径、纯标准库）。

## 6. 降级策略

- **缺数据/缺软件**：如实写 `status=insufficient_evidence` 或 `partial`，在
  `errors`/`degradations`/`uncertainty`/`limitations` 中说明，**禁止编造**文件内容、
  哈希、元数据或运行结果。
- **输出契约缺失**：脚本仍产出仅含元数据字段的合法 result.json，并在 `uncertainty`
  标 `output_contract` 缺失。
- **超大文件/符号链接/不可读/非常规文件**：跳过内容哈希，保留路径、大小、权限、链接
  目标，记录 `too_large`/`symlink`/`unreadable`/`non_regular`/`binary`。
- **SQLite 打不开/坏表**：记录 `sqlite_open_error`/`sqlite_query_error`，不崩溃。
- **写盘失败**：保留已落盘骨架，退出码 2，不崩溃；重启后可续跑 `--update`。

## 7. 边界

只解决"整理 + 证据 + 版本 + 别名 + 恢复对账 + 索引 + 检索问答与章节定位"这一类任务；
不做通用文件系统、不做内容级深度语义理解（由 Agent 在脚本候选上补充）；没有真实材料
时不生成示例答案。

## 最小调用示例（示例，非正式案例结果）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work \
  --case-json /input/case.json --task-path /input/task.md
# 示意输出: finalize: 12 files, 2 dup groups, 1 version candidates, 1 broken symlinks, 0 reconcile missing -> /app/output/result.json
```
