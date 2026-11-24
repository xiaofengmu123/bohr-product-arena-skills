---
name: document-library-repair
description: 科研资料库与文件管理（document library repair）：只读盘点文件、目录、权限与符号/硬链接别名，按内容 SHA-256 去重并按内容哈希/修改时间重建候选版本链，解析行政版本说明与库目录（JSON/CSV/BibTeX/RIS/SQLite），恢复对账（matches/mismatches/missing/orphaned）并重建可检索索引，回答常见检索问题，产出可复核的清单、目录树、恢复报告与操作日志。本版以鲁棒性为侧重点：空输入、坏 JSON、缺参数、坏编码、超大文件、断链均优雅降级不崩溃。当需要整理科研资料库、维护元数据与版本关系、修复缺失/断链/重复或产出可交接清单时使用。
version: 2.0.0
---

# 科研资料库修复 Skill（鲁棒性版）

## 1. 角色与何时使用

面向"document library repair"：来源混杂、命名不一、存在重复、多版本、别名、断链与不一致元数据的科研资料库，整理成可检索、可交接、可复跑的结构化结果。核心价值是**动手落库 + 留证据**，不只给建议。

本版设计取舍明确偏向**可靠体验与异常处理**：所有失败路径都先落盘、再降级、后标注，任何单点坏文件都不拖垮整体运行；语义判定由 Agent 在脚本给出的机械候选上补充，脚本负责确定性、可复核的机械层。

适用：文件入库组织、目录归位与去重、元数据抽取与溯源、版本链重建、缺失/断链恢复、检索问答、长期归档交接。不适用：一次性脚本、纯聊天问答、无需可复核证据的场景。

## 2. 输入契约（运行时，只读）

- `/input/case.json`：案例标识、任务说明与 `output_contract`（键名可能是 `output_contract`/`output`/`result_schema`/`contract` 等，脚本自动识别）。
- `/input/task.md`：题目规则、组织约定、字段要求、行政版本说明与完成标准。
- `/input/` 其余文件：科研材料。**以真实文件内容为准，不依据领域标签补造数据。**

工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`。硬约束：所有源文件只读，不移动、不覆盖、不删除；同一输入重复运行结果一致（遍历排序、哈希稳定、无随机数；仅时间戳不同）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`：从 case.json 或 task.md 识别，非空。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注。
- 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`/`{}`，不改名、不删未知键；与保留键冲突时改存 `computed._contract_<字段>`）。
- `sources`：关键结论的证据来源（`locator` + 逐字 `quote`）。
- `uncertainty`：不确定项、原因与严重度。
- `change_log`：操作时间线（`time`/`actor`/`step`/`detail`）。
- 辅助字段：`errors`、`degradations`、`runtime`、`limitations`、`pending_human`；机械证据统一放 `computed`（`files`/`summary`/`duplicate_groups`/`version_candidates`/`version_notes`/`aliases`/`reconciliation`/`answers`/`index`/`artifacts`）。

其余可编辑产物（写入 `/app/output/`）：`library_manifest.csv`（文件清单）、`directory_tree.txt`（目录树，空目录标 `[空目录]`）、`dedup_version.json`（重复组、版本候选、版本说明、别名）、`recovery_report.json`（对账）、`library_index.json`（可检索索引）、`library_manifest.md`（交接报告）、`operation_log.jsonl`（操作日志）。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

原则：**先落盘、再分析、后增量改进**；任一阶段失败都不得留下空产物。前 3 分钟内必须先落盘一版 `status=partial` 的 result.json 骨架，之后只做增量改进。

| 步骤 | 输入 | 动作与输出 | 时间预算 |
|---|---|---|---|
| 0 骨架 | case.json + task.md | 立即执行 `python3 -B scripts/main.py --skeleton-only`，写出含 `case_id` + contract 占位 + `status=partial` 的 result.json；同时读题提取组织规则、字段要求与版本说明 | ≤ 3 分钟（必须完成落盘） |
| 1 机械盘点 | /input/ 全目录 | 执行 `python3 -B scripts/main.py`（内部：盘点→去重→版本→别名→版本说明→恢复对账→索引→问答→产物），产出全部可编辑产物并写完整 result.json | ≤ 2 分钟 |
| 2 语义归位 | 清单 + 规则 | 按题目规则确认目录组织、去重判定、版本链、元数据字段与问答；语义结论写成 patch，用 `python3 -B scripts/main.py --update patch.json --result /app/output/result.json` 增量合并 | 剩余约 6–8 分钟 |
| 3 证据与复核 | result.json + 产物 | 为每个关键结论补 `sources`（locator+quote）与 `uncertainty`；执行 `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` | ≤ 2 分钟 |
| 4 定稿 | 复核通过 | 将 `status` 改为 `ok`，或按实际缺口保留 `partial`/`insufficient_evidence`/`input_error`；确认 contract 字段齐全 | 收尾 |

模型请求纪律：脚本读写不消耗模型请求；64 次请求优先用于读题、读材料、语义判定与复核。每步完成后用 `--update` 落盘，避免最后一次性写结果丢失进度。

## 5. 证据溯源（硬要求）

- 每个关键结论/数值/重复或版本判定，都要有 `sources` 条目：`{ "item": ..., "locator": "materials/xxx.pdf#p3" 或文件相对路径, "quote": "逐字原文" }`。
- 脚本自动为机械结论补证据（如重复组 `sha256=… count=…`、断链路径）；Agent 负责为语义结论补原文引用。
- 冲突不隐藏：同一指标两个来源不一致时都记录并标 `source_conflict`。
- 不虚构引用、DOI、哈希、数值；缺失写 `not_reported` 或放入 `pending_human`。

## 6. 自检清单（提交前逐条勾选，对应评分维度）

**实际效果**
- [ ] 已产出真实清单/目录树/版本记录/恢复报告/索引，而非仅建议；落库文件与源文件一一对应，哈希一致。
- [ ] 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六类要素；难处理文件未被静默跳过。

**使用增益**
- [ ] 去重按内容 SHA-256 而非文件名；版本链按归一化主干 + 内容差异给候选，并标注为启发式。
- [ ] 库目录（JSON/CSV/BibTeX/RIS/SQLite）自动解析并恢复对账；机械问答带证据定位。

**创新增益**
- [ ] 别名识别（断链/硬链接）、行政版本说明解析、恢复对账与索引重建均已落盘。

**可靠体验（本版重点）**
- [ ] 空输入、坏 JSON、缺参数、坏编码、超大文件、断链、无权限目录均不崩溃，逐条记录进 `errors`/`degradations`。
- [ ] `result.json` 通过 `--check`；`status` 诚实，`case_id` 非空，contract 字段齐全；`python3 -B scripts/selftest.py` 退出码 0。

**生态贡献**
- [ ] `SKILL.md` frontmatter 含 name/description/version；脚本与 `references/` 路径有效；调用示例已标"示例"；只依赖标准库。

## 7. 降级策略

- **缺数据/缺软件**：如实写 `status=insufficient_evidence`/`partial`，在 `errors`/`degradations`/`uncertainty`/`limitations` 中说明，**禁止编造**文件内容、哈希、元数据或运行结果。
- **缺 case.json（但有材料）**：`status=input_error`；**空输入目录**：`status=insufficient_evidence`，均退出码 0。
- **坏 JSON / 坏编码**：记录 `json_error`/`bad_encoding`，继续处理其余材料，不崩溃。
- **输出契约缺失**：仅元数据字段的合法 result.json，并在 `uncertainty` 标 `output_contract` 缺失。
- **超大文件**：跳过内容哈希（默认 256 MiB），保留路径、大小、权限、链接目标，记录 `too_large`。
- **符号链接/硬链接**：符号链接（含断链）与硬链接组独立记录；`symlink`/`broken_symlink`。
- **SQLite 打不开/坏表**：记录 `sqlite_open_error`/`sqlite_query_error`，不崩溃。
- **写盘失败**：保留已落盘骨架，退出码 2；重启后可续跑 `--update`。

## 8. 边界与不适用

只解决"整理 + 证据 + 版本 + 别名 + 恢复对账 + 索引 + 检索问答"这一类任务；不做通用文件系统管理，不做内容级语义理解（由 Agent 在脚本候选上补充）；没有真实材料时不生成示例答案，只返回"材料不足"。

## 9. 最小调用示例（示例，非正式案例结果）

```bash
python3 -B scripts/main.py --case-json /input/case.json --task-path /input/task.md \
  --input /input --output /app/output --work /app/work
# 示意输出: finalize: 12 objects, 2 dup groups, 1 version candidates, 1 broken symlinks, 0 missing -> /app/output/result.json (status=ok)
```

缺省即 `/input`、`/app/output`、`/app/work`，可直接 `python3 -B scripts/main.py`。自检：`python3 -B scripts/selftest.py`。
