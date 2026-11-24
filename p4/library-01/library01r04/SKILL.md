---
name: research-library-curation
description: 科研资料库与文件管理（document library curation）：只读盘点文件、目录、权限、符号链接与硬链接别名，按内容 SHA-256 去重并按“归一化主干 + 内容哈希 + 修改时间”重建启发式版本候选链，解析行政版本说明与库目录（JSON/CSV/BibTeX/RIS/SQLite），做恢复对账（matches/mismatches/missing/orphaned）并重建可检索索引，回答机械检索问题，产出可复核清单、目录树、恢复报告与操作日志；对每一条不确定与降级情况显式记录 limitations/uncertainty，缺数据绝不编造。当需要整理科研资料库、维护元数据与版本关系、修复缺失/断链/重复，或产出可交接、可复跑的结构化证据时使用。
version: 2.0.0
---

# 科研资料库整理 Skill（降级与不确定性显式版）

## 1. 角色与何时使用

面向真实、混杂的科研资料库：来源不一、命名重复、存在多版本、别名、断链与不一致
元数据。本 Skill 的价值是**动手落库 + 留证据 + 诚实标注不确定**，而不是只给建议。

适用：文件入库盘点、目录归位与去重、元数据/来源提取、版本链重建、缺失/断链恢复、
检索问答、长期归档交接。不适用：一次性脚本、纯聊天问答、无真实材料时补造答案。

本版设计取舍：机械层交给确定性脚本，语义层留给 Agent；所有不确定项、降级项、
局限显式写入 `uncertainty`/`degradations`/`limitations`，**材料不足即如实标注**，
不生成示例答案、不推测领域事实。

## 2. 输入契约（运行时，只读）

- `/input/case.json`：案例标识与 `output_contract`（键名可能是 `output_contract`/
  `output`/`result_schema`/`contract`/`schema`，脚本自动识别并原样回填）。
- `/input/task.md`：题目规则、组织约定、行政版本说明与完成标准。
- `/input/` 其余文件：科研材料（文件、目录、库清单、版本说明、权限信息等）。
  **以真实文件内容为准，不依据领域标签补造数据。**

工作目录 `/app/work/`；最终产物 `/app/output/`。硬约束：源文件只读，不移动、
不覆盖、不删除；同一输入重复运行，除时间戳外结果一致（遍历排序、哈希稳定、无随机数）。

## 3. 输出契约

必须产出 `/app/output/result.json`，含：

- `case_id`：从 case.json / task.md 识别，非空。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注。
- 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`；与保留键
  冲突时改存 `computed._contract_<字段>`）。
- `sources`：关键结论的证据（locator + 逐字 quote）。
- `uncertainty`：不确定项 + 原因 + 严重度（high/medium/low）。
- `pending_human` / `errors` / `degradations` / `runtime` / `limitations` /
  `change_log`：操作时间线（time/actor/step/detail）。
- 机械证据统一放 `computed`（files/summary/inventory/duplicate_groups/
  version_candidates/version_notes/aliases/reconciliation/answers/index）。

其余可编辑产物（写入 `/app/output/`）：`library_manifest.csv`、`directory_tree.txt`
（空目录标 `[空目录]`）、`dedup_version.json`、`recovery_report.json`、
`library_index.json`、`library_manifest.md`、`operation_log.jsonl`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 每响应 ≤16384 token）

原则：**先落盘、再分析、后增量改进**。任一阶段失败都不得留下空产物。

| 步骤 | 输入 | 动作与输出 | 时间预算 |
|---|---|---|---|
| 0 骨架 | case.json + task.md | **立即**执行 `python3 -B scripts/main.py --skeleton-only`，写出含 `case_id` + contract 占位 + `status=partial` 的 result.json；读 task.md 提取组织规则与版本说明 | ≤ 3 分钟（必须完成落盘） |
| 1 盘点与修复 | /input/ 全目录 | 执行 `python3 -B scripts/main.py`（内部：盘点→去重→版本→别名→版本说明→恢复对账→索引→问答→产物），产出全部产物并写完整 result.json | ≤ 2 分钟 |
| 2 语义归位 | 清单 + 规则 | 按题目规则确认目录组织、去重、版本链、元数据字段与问答；把结论写成 patch.json，用 `python3 -B scripts/main.py --update patch.json --result /app/output/result.json` 增量合并 | 剩余时间（约 6–8 分钟） |
| 3 证据与复核 | result.json + 产物 | 为每个关键结论补 `sources`（locator + quote）与 `uncertainty`；执行 `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` | ≤ 2 分钟 |
| 4 定稿 | 复核通过 | 把 `status` 改为 `ok`，或按实际缺口保留 `partial`/`insufficient_evidence`；确认 contract 字段已填 | 收尾 |

模型请求纪律：脚本读写不消耗模型请求；64 次请求优先用于读题、读材料、语义判定与
复核。每步用 `--update` 落盘，避免最后一次性写结果丢失进度。**超过 60 秒的等待、
重复失败两次以上的动作必须停止并如实记录。**

## 5. 证据溯源与不确定性（本版重点）

- 每个关键结论/数值/重复或版本判定都要有 `sources`：`{ "item", "locator",
  "quote" }`；`locator` 为 `materials/xxx.pdf#p3` 或文件相对路径，`quote` 为逐字原文。
- 冲突不隐藏：同一指标两个来源不一致时都记录并标 `source_conflict`。
- 不确定项必须写入 `uncertainty` 并给严重度：启发式版本链=medium、断链=high、
  编码回退=medium、目录清单缺失=medium。
- 降级/失败写入 `degradations`（坏 JSON、坏编码、超大文件、无权限、非常规文件、
  SQLite 打不开等）；能力边界写入 `limitations`。**缺失写 `not_reported` 或放入
  `pending_human`，绝不虚构哈希、DOI、数值或文件内容。**

## 6. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] 实际效果：已产出真实清单/目录树/版本记录/恢复报告，而非仅建议；落库文件与源文件一一对应、哈希一致。
- [ ] 实际效果：覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六类要素；难处理文件未被静默跳过。
- [ ] 可核验：`library_manifest.csv`、`operation_log.jsonl`、`dedup_version.json`、`recovery_report.json`、`library_index.json` 均存在，他人可按记录复跑核对。
- [ ] 使用增益：去重按内容哈希而非文件名；版本链按归一化主干 + 内容差异给候选，并标注为启发式。
- [ ] 创新增益：别名识别（断链/硬链接）、行政版本说明解析、恢复对账与索引重建均已落盘。
- [ ] 可靠体验：空目录、坏 JSON、坏编码、缺权限、超大文件均不崩溃，记录进 `errors`/`degradations`。
- [ ] 可靠体验：`result.json` 通过 `--check`；`status` 诚实；`case_id` 非空；contract 字段齐全。
- [ ] 文档：`SKILL.md`、脚本与 `references/` 路径有效；调用示例标明“示例”，未冒充正式案例结果。

## 7. 降级策略（缺数据不编造）

- 缺 case.json → `status=input_error` 并记录；缺 output_contract → 仅元数据字段 +
  `uncertainty` 标注。
- 空输入目录 → 退出码 0、`status=insufficient_evidence`，不抛异常。
- 超大文件/符号链接/不可读/非常规文件 → 跳过内容哈希，保留路径、大小、权限、链接
  目标，记录 `too_large`/`symlink`/`unreadable`/`non_regular`。
- 坏 JSON/坏编码/SQLite 坏表 → 记录 `parse_error`/`decode_fallback`/
  `sqlite_open_error`，继续处理其余材料。
- 写盘失败 → 保留已落盘骨架，退出码 2，不崩溃；重启后可 `--update` 续跑。
- 语义字段（作者/年份/项目号）仅在源文本有逐字证据时填写；否则 `not_reported`。

## 8. 边界

只解决“整理 + 证据 + 版本 + 别名 + 恢复对账 + 索引 + 检索问答”；不做通用文件系统
或内容级语义理解（由 Agent 在脚本候选上补充）；没有真实材料时不生成示例答案。

## 最小调用示例（示例，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --case-json /input/case.json --task-path /input/task.md \
  --input-root /input --output-dir /app/output --work-dir /app/work
# 示意输出: finalize: wrote /app/output/result.json
```

自检（不依赖正式案例，可随时执行）：

```bash
python3 -B scripts/selftest.py
python3 -B scripts/main.py --selftest
python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json
```
