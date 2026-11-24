---
name: research-library-repair
description: 科研资料库与文件管理（document library repair）：只读盘点文件/目录/权限/符号与硬链接别名，按内容 SHA-256 去重、按归一化主干与内容差异重建候选版本链，解析行政版本说明与库目录（JSON/CSV/BibTeX/RIS/SQLite），恢复对账（matches/mismatches/missing/orphaned）并重建可检索索引，回答常见检索问题，产出可复核的清单、目录树、恢复报告、索引与操作日志。当需要整理科研资料库、维护元数据与版本关系、修复缺失/断链/重复或产出可交接清单时使用。
version: 2.0.0
---

# 科研资料库修复 Skill

## 1. 角色与何时使用

面向"document library repair"：来源混杂、命名不一、存在重复、多版本、别名、断链与不一致
元数据的科研资料库，整理成可检索、可交接、可复跑的结构化结果。核心价值是**动手落库 + 留
证据**：不只给建议，而是产出清单、哈希、目录树、别名、版本关系、恢复报告、索引与操作日志。

适用：文件入库组织、目录归位与去重、元数据抽取与溯源、版本链重建、缺失/断链恢复、检索问答、
长期归档交接。不适用：一次性脚本、纯聊天问答、无需可复核证据的场景。

## 2. 输入契约（运行时，只读）

- `/input/case.json`：案例标识、任务说明与 `output_contract`（键名可为
  `output_contract`/`output`/`result_schema`/`contract`，脚本自动识别）。
- `/input/task.md`：题目规则、组织约定、字段要求、行政版本说明与完成标准。
- `/input/` 其余文件：科研材料（文件、目录、库目录/清单、版本说明、权限信息等）。
  **以真实文件内容为准，不依据领域标签补造数据。**

工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`。硬约束：源文件只读，不移动、
不覆盖、不删除；同一输入重复运行结果一致（遍历排序、哈希稳定、无随机数）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`：从 case.json / task.md 识别。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注。
- 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`，不改名、不删未知键）。
- `sources`（关键结论，locator + 逐字 quote）、`uncertainty`、`change_log`。
- 辅助字段 `errors`/`degradations`/`runtime`/`limitations`/`pending_human`；机械证据统一放
  `computed`（files/dirs/summary/duplicate_groups/version_candidates/version_notes/
  aliases/catalogs/reconciliation/index/answers/artifacts）。

其余可编辑产物（写入 `/app/output/`）：`library_manifest.csv`、`directory_tree.txt`、
`dedup_version.json`、`recovery_report.json`、`library_index.json`、`library_manifest.md`、
`operation_log.jsonl`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

原则：**先落盘、再分析、后增量改进**；任一阶段失败不得留下空产物。前 3 分钟必须落盘一版
`status=partial` 的结果骨架，之后只做增量改进。

| 步骤 | 输入 | 动作与输出 | 时间预算 |
|---|---|---|---|
| 0 骨架 | case.json + task.md | 立即执行 `python3 -B scripts/finalize.py --skeleton-only`，写出含 case_id + contract 占位 + `status=partial` 的 result.json；同时读 task.md 提取组织规则、字段要求与版本说明 | ≤ 3 分钟（必须落盘） |
| 1 盘点与修复 | /input/ 全目录 | 执行 `python3 -B scripts/finalize.py`（内部：盘点→去重→版本→别名→版本说明→对账→索引→问答→产物），写出全部可编辑产物与完整 result.json | ≤ 2 分钟 |
| 2 语义归位 | 清单 + 规则 | 按题目规则确认目录组织、去重判定、版本链、元数据字段与问答；语义结论写 patch，用 `python3 -B scripts/finalize.py --update patch.json --result /app/output/result.json` 增量合并 | 剩余时间（约 6–8 分钟） |
| 3 证据与复核 | result.json + 产物 | 为每个关键结论补 `sources`（locator+quote）与 `uncertainty`；执行 `python3 -B scripts/finalize.py --check /app/output/result.json --case-json /input/case.json` | ≤ 2 分钟 |
| 4 定稿 | 复核通过 | 将 `status` 改为 `ok`，或按实际缺口保留 `partial`/`insufficient_evidence`；确保 contract 字段齐全 | 收尾 |

模型请求纪律：脚本读写不消耗模型请求；64 次请求优先用于读题、读材料、语义判定与复核。
每步用 `--update` 落盘，避免最后一次性写结果丢失进度。

## 5. 证据溯源（硬要求）

- 每个关键结论/数值/重复或版本判定都要有 `sources` 条目：`{"item":…, "locator":…, "quote":"逐字原文"}`。
- 冲突不隐藏：同一指标两个来源不一致时都记录并标 `source_conflict`。
- 不虚构引用、DOI、哈希、数值；缺失写 `not_reported` 或放入 `pending_human`。

## 6. 自检清单

执行 `python3 -B scripts/selftest.py`，并逐条勾选 `references/checklist.md`。提交前至少保证：
空目录、坏 JSON、坏编码、缺 case.json、缺 output_contract、写盘失败、重复运行七类场景
不崩溃且状态诚实；`result.json` 通过 `--check`。

## 7. 降级策略

- **缺数据/缺软件**：如实写 `status=insufficient_evidence` 或 `partial`，在
  `errors`/`degradations`/`limitations` 说明，**禁止编造**文件内容、哈希、元数据或运行结果。
- **输出契约缺失**：脚本仍产出仅含元数据字段的合法 result.json，并在 `uncertainty` 标
  `output_contract` 缺失。
- **超大文件/符号链接/不可读/非常规文件**：跳过内容哈希，保留路径、大小、权限、链接目标，
  记录 `too_large`/`symlink`/`unreadable`/`non_regular`。
- **SQLite 打不开/坏表**：记录 `sqlite_open_error`/`sqlite_query_error`，不崩溃。
- **写盘失败**：保留已落盘骨架，退出码 2，不崩溃；重启后可续跑 `--update`。

## 8. 边界

只解决"整理 + 证据 + 版本 + 别名 + 恢复对账 + 索引 + 检索问答"这一类任务；不做通用文件系统、
不做内容级语义理解（由 Agent 在脚本候选上补充）；没有真实材料时不生成示例答案。

## 最小调用示例（示例，非正式案例结果）

```bash
python3 -B scripts/finalize.py --case-json /input/case.json --task-path /input/task.md \
  --input-root /input --output-dir /app/output --work-dir /app/work
# 示意输出: finalize: 12 files, 2 dup groups, 1 version candidates, 1 broken symlinks,
#            reconcile 3/1/2 -> /app/output/result.json
```
