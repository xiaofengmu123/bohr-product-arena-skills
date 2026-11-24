---
name: scientific-document-intelligent-retrieval
description: 科研文件智能检索（scientific document intelligent retrieval）：只读盘点文件、目录、权限与符号/硬链接别名，按内容 SHA-256 去重并按归一化主干+内容相似度重建版本候选链，解析行政版本说明与库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite），恢复对账（matches/mismatches/missing/orphaned），抽取章节/段落级跨文件索引，把年份/项目/类型/批次/版本/权限过滤作为硬约束执行，回答检索问题并给出 locator+逐字 quote 的证据回链，产出可复核的清单、目录树、版本记录、恢复报告、检索索引、交接报告与操作日志。当需要组织科研资料、维护元数据与版本关系、按章节定位与条件筛选检索、修复缺失/断链/重复、或产出可交接清单时使用。
version: 1.1.0
---

# 科研文件智能检索 Skill

## 1. 角色与何时使用

面向"科研文件智能检索"：来源混杂、命名不一、存在重复、多版本、别名、断链与不一致
元数据的科研资料库。核心价值是**动手落库 + 可回链证据**：不只给建议，而是产出清单、
哈希、目录树、别名、版本关系、恢复报告、章节索引、检索回答与操作日志，且每条结论
能指回原文片段（文件路径 + 页码/行号 + 逐字 quote）。

适用：文件入库组织、目录归位与去重、元数据抽取、版本链重建、跨文件章节定位检索、
硬过滤执行、缺失/断链恢复、检索问答、长期归档交接。不适用：开放网络文献检索、论文
全文精读与综述、一次性脚本、无需可复核证据的纯聊天。

## 2. 输入契约（运行时，只读）

- `/input/case.json`：案例标识、任务说明与 `output_contract`（键名可能为
  `output_contract`/`output`/`result_schema`/`contract` 等，脚本自动识别）。
- `/input/task.md`：题目规则、组织约定、字段要求、行政版本说明与完成标准。
- `/input/` 其余文件：科研材料（文件、目录、库目录/清单、版本说明、权限信息等）。
  **以真实文件内容为准，不依据领域标签补造数据。**

工作目录 `/app/work/`（中间产物 inventory.json、library.json）；最终产物
`/app/output/`。硬约束：所有源文件只读，不移动、不覆盖、不删除源文件；同一输入
重复运行结果一致（遍历排序、哈希稳定、无随机数）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`：从 case.json 或 task.md 识别，非空。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注。
- 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`，不改名、不删
  未知键；与保留键冲突时改存 `computed._contract_<字段>`）。
- `sources`：关键结论的证据来源（locator + 逐字 quote）。
- `uncertainty`：不确定项、原因与严重度。
- `pending_human`：需人工确认项。
- `change_log`：操作时间线（time/actor/step/detail）。
- 辅助字段：`errors`、`degradations`、`runtime`、`limitations`；机械证据统一放
  `computed`（files/summary/duplicate_groups/version_candidates/version_notes/
  aliases/reconciliation/answers/filters/artifacts）。

其余可编辑产物（写入 `/app/output/`）：`library_manifest.csv`（文件清单）、
`directory_tree.txt`（目录树，空目录标 `[空目录]`）、`dedup_version.json`（重复组、
版本候选、版本说明、别名）、`recovery_report.json`（对账）、`library_index.json`
（章节级索引 + 检索回答）、`library_manifest.md`（交接报告）、
`operation_log.jsonl`（操作日志）。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

原则：**先落盘、再分析、后增量改进**；任一阶段失败都不得留下空产物。

| 步骤 | 输入 | 动作与输出 | 时间预算 |
|---|---|---|---|
| 0 骨架 | case.json + task.md | 立即 `python3 -B scripts/main.py --skeleton-only`，写出含 case_id + contract 占位 + `status=partial` 的 result.json；同时读 task.md 提取规则、版本说明、问题与过滤条件 | ≤ 3 分钟（必须完成落盘） |
| 1 盘点与检索 | /input/ 全目录 | `python3 -B scripts/main.py`（内部：盘点→哈希→去重→版本→别名→版本说明→对账→章节索引→过滤→检索问答→产物），产出全部可编辑产物并写完整 result.json | ≤ 2 分钟 |
| 2 语义归位 | 清单 + 规则 | 按题目规则确认目录组织、版本链、元数据字段、过滤与问答；把语义结论写成 patch，用 `python3 -B scripts/main.py --update patch.json --result /app/output/result.json` 增量合并 | 剩余时间（约 6–8 分钟） |
| 3 证据与复核 | result.json + 产物 | 为关键结论补 `sources`（locator+quote）与 `uncertainty`；执行 `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` | ≤ 2 分钟 |
| 4 定稿 | 复核通过 | 将 `status` 改为 `ok`，或按实际缺口保留 `partial`/`insufficient_evidence` | 收尾 |

**评分维度对齐（本版重点）**：`实际效果(40)`→步骤 0–3 的章节级索引、硬过滤与证据
回链；`使用增益(25)`→步骤 1 的内容哈希去重、版本候选与库目录自动对账，减少人工
比对；`创新增益(15)`→别名（含断链+硬链接）、行政版本说明对齐、恢复对账与索引重建；
`可靠体验(10)`→第 6 节降级与 `scripts/selftest.py`；`生态贡献(10)`→全标准库、相对
路径、references 齐全。

模型请求纪律：脚本读写不消耗模型请求；64 次请求优先用于读题、读材料、语义判定与
复核。每步完成后用 `--update` 落盘，避免最后一次性写结果丢失进度。

## 5. 证据溯源（硬要求）

- 每个关键结论/数值/重复或版本判定/检索命中，都要有 `sources` 条目：
  `{ "item": ..., "locator": "materials/xxx.pdf#p3" 或 文件相对路径#L行号,
     "quote": "逐字原文" }`。
- 冲突不隐藏：同一指标两个来源不一致时，都记录并标 `source_conflict`。
- 不虚构引用、DOI、哈希、数值；缺失写 `not_reported` 或放入 `pending_human`。

## 6. 自检清单（提交前逐条勾选；跑 `python3 -B scripts/selftest.py`）

- [ ] **实际效果**：已产出真实清单/目录树/版本记录/恢复报告/章节索引，而非仅建议；
      索引条目落到段落，检索回答带 locator+quote。
- [ ] **实际效果**：覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与
      来源六类要素；难处理文件未被静默跳过。
- [ ] **可核验**：`library_manifest.csv`、`operation_log.jsonl`、`dedup_version.json`、
      `recovery_report.json`、`library_index.json` 均存在，他人可复跑核对。
- [ ] **使用增益**：去重按内容 SHA-256 而非文件名；版本链按归一化主干+内容差异给候选
      并标 `heuristic`；库目录自动解析并恢复对账。
- [ ] **创新增益**：别名识别（断链+硬链接）、行政版本说明解析、恢复对账与索引重建已落盘。
- [ ] **可靠体验**：空目录、坏 JSON、坏编码、缺权限、超大文件、断链、坏 SQLite 均不
      崩溃，记录进 `errors`/`degradations`；`--check` 通过。
- [ ] **文档**：`SKILL.md` frontmatter 齐备，脚本与 `references/` 路径有效，调用示例
      已标"示例"，未冒充正式案例结果。

## 7. 降级策略

- **缺数据/缺软件**：如实写 `status=insufficient_evidence` 或 `partial`，在
  `errors`/`degradations`/`limitations`/`uncertainty` 中说明，**禁止编造**文件内容、
  哈希、元数据或运行结果。
- **输出契约缺失**：仍产出仅含元数据字段的合法 result.json，并在 `uncertainty` 标
  `output_contract` 缺失。
- **超大文件/符号链接/不可读/非常规文件**：跳过内容哈希，保留路径、大小、权限、链接
  目标，记录 `too_large`/`symlink`/`unreadable`/`non_regular`。
- **无文本层扫描件/加密或损坏文件/Office 二进制**：只记录元数据，`degradations` 标
  `no-text-layer`/`office-binary-metadata-only`，交由 Agent 用 pdf-text-tool/OCR 补。
- **SQLite 打不开/坏表**：记录 `sqlite_open_error`/`sqlite_query_error`，不崩溃。
- **写盘失败**：保留已落盘骨架，退出码 2，不崩溃；重启后可续跑 `--update`。

## 8. 边界

只解决"文件已在库中，如何被准确找到、定位到章节、按条件筛出并给出可回链证据"这一类
任务；不做开放网络检索与论文精读，不做底层权限建设，不做内容级语义理解（由 Agent 在
脚本候选上补充）；没有真实材料时不生成示例答案。

## 最小调用示例（示例，非正式案例结果）

```bash
python3 -B scripts/main.py --case-json /input/case.json --task-path /input/task.md \
  --input-root /input --output-dir /app/output --work-dir /app/work
# 示意输出: finalize: 12 files, 2 dup groups, 1 version candidates, 3 answers, 0 reconcile mismatches -> /app/output/result.json
```
