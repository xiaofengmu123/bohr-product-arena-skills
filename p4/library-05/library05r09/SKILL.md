---
name: library-repair-lite
description: 科研资料库修复 Skill（精简可读版）：对给定文件库做确定性盘点、sha256 去重、版本/别名关系识别、关键词检索与可复跑交接清单。适用于"阅读批注与知识沉淀"赛道的真实文件库修复输入（文件、别名、行政版本备注、元数据、检索问题、恢复/索引操作）；先发现再假设、骨架优先、证据逐条溯源、缺料如实降级。
version: 1.0.0
---

# 科研资料库修复：盘点 · 去重 · 版本/别名 · 检索 · 交接

## 1. 角色与何时使用

本 Skill 解决一类明确问题：科研资料散落在多处（多版本、重复下载、别名、临时笔记、元数据），
需整理成"可检索、可交接、可复跑核对"的清单，并保留版本与别名关系。

**适用**：给定 `/input` 目录，盘点文件与权限、识别重复/版本/别名/冲突、建立检索索引、
回答检索问题、记录跳过/失败/冲突与恢复方式。

**不适用**：文献语义判断、学术写作、长期归档制度。

**关键判断**：实际输入画像是"真实文件库 + 别名 + 行政版本备注 + 元数据 + 检索问题 + 恢复/索引操作"。
因此本实现为确定性盘点引擎（哈希去重、归一化 stem 版本分组、显式关系读取、关键词检索），
而非 PDF 批注语义提取。文本层/OCR 属外部工具职责；缺失时如实标注，不编造。

## 2. 输入契约

- 运行时输入在 `/input`：`case.json`（任务与 `output_contract`）与 `task.md`，材料在 `/input/` 下。
- 工作目录 `/app/work`，最终产物写 `/app/output/`。
- 所有子命令支持 `--input/--work/--output` 覆盖默认目录；默认路径也必须可直接运行。
- **先发现再假设**：不假设 `case.json` 固定键名、不假设材料文件名；`output_contract` 支持 dict/list。
- 文本按 `utf-8-sig` 容错读取；坏 JSON/坏编码/不可读文件都降级记录，不崩溃；退出码恒为 0。

## 3. 输出契约

`/app/output/result.json` 必含：`case_id`（原样带回）、`output_contract` 全部字段（逐键回填，
缺值 `null`/`[]`，不改名、不删未知键）、信封字段 `status/sources/uncertainty/pending_human/change_log/
errors/degradations/runtime`，另附 `result/claims/uncertainties`。

| 文件 | 内容 |
|---|---|
| `library_index.json` | 完整盘点：文件清单（无文本层）+ 分析（别名/版本/冲突/索引），确定性无时间戳 |
| `evidence_map.json` | 逐条 claim 的 `source_id + locator + quote` 映射 |
| `inventory.md` | 可读交接清单（可打开、可编辑） |

**溯源硬要求**：每条结论都要能指回材料逐字片段（`source_id + locator + quote`）；冲突不隐藏
（同指标两来源不一致都记录并标 `source_conflict`）；缺值写 `pending_human`/`not_reported`，不虚构。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须落盘一版 `result.json` 骨架（`status=partial`），之后只做增量改进。**
接近时限立即 `build` 收口，绝不留空产物。

| 步骤 | 输入 | 输出 | 时间 |
|---|---|---|---|
| 1. `scripts/main.py init` | `/input` | `work/scan.json`、`work/draft.json` 模板、`output/result.json`（partial） | ≤60s |
| 2. `scripts/main.py show` | `work/scan.json` | 打印带 locator 的文件/分组/特殊文件/问题 | ≤30s |
| 3. 阅读 + 写 `work/draft.json` | show 输出 | 模型只补语义判断（版本备注、别名裁定、检索答案、关系），每条带 evidence | 约 30–40 次请求 |
| 4. `scripts/main.py check` | `work/draft.json` | `work/check_report.json`（结构 + source 存在 + quote 逐字命中） | ≤30s |
| 5. `scripts/main.py build` | scan + draft | `result.json` + 三个产物，无条件收口 | ≤60s |
| 6. `scripts/main.py selfcheck` | `/app/output` | 复检产物齐全可解析 | ≤30s |

`run` 子命令提供无 draft 的确定性基线（`init→build→selfcheck`，status≤partial）；它只防 0 分，
不能拿完整分。**只跑 `build` 不读材料不写 draft 视为未完成。**

`work/draft.json` 结构：

```json
{"case_id":"…",
 "notes":[{"target":"F004","kind":"alias|version|metadata|correction|query_answer|conflict|recovery|skip",
           "text":"…","basis":"…","confidence":"explicit|inferred",
           "evidence":[{"source_id":"F004","locator":"materials/x.pdf","quote":"逐字片段"}]}],
 "queries":[{"query_id":"Q1","answer":"…","status":"answered|no_direct_match|insufficient_evidence",
             "evidence":[{"source_id":"F004","locator":"…","quote":"…"}]}],
 "relations":[{"type":"version|alias|conflict","members":["F001","F002"],"canonical":"F001","reason":"…"}]}
```

## 5. 自检清单（build 后逐项核对，对应评分维度）

- [ ] **完成度（15）**：`result.json` 可解析，`case_id` 与输入一致；文件/重复/版本/别名/冲突一一对应。
- [ ] **正确性（15）**：每条 claim 的 `quote` 在材料中逐字命中；定位用 `rel_path + sha256`，不张冠李戴。
- [ ] **完整性（5）**：覆盖文件类型、元数据字段、目录层级、重复、版本差异、权限与来源。
- [ ] **可核验性（5）**：`evidence_map.json` 含 locator+quote；同输入两次运行除 `runtime` 外逐字节一致。
- [ ] **质量增益（15）**：`show` 一步给出带定位的盘点，人工返工少；`check` 拦截编造与错源。
- [ ] **效率/可验证（5+5）**：主路径五命令退出码 0；`selftest.py` 同输入对照证明改善。
- [ ] **可靠体验（10）**：空输入/坏 JSON/缺目录/无文本层/损坏/权限不足均降级不崩溃，退出码恒 0。
- [ ] **生态贡献（10）**：`SKILL.md`/`references/contract.md` 说明齐全；脚本纯标准库、相对路径、可独立复跑。

## 6. 降级策略（缺数据/缺软件如实标注，绝不编造）

| 场景 | 处置 |
|---|---|
| 材料不足 / 无文本层 | `status=insufficient_evidence`，缺口写 `pending_human` |
| 缺外部工具（pdftotext/OCR/office） | 只做字节级盘点，`degradations` 标 `no_tool:<name>`，不假装有文本层 |
| 加密/损坏/权限不足/超大文件 | `errors`/`degradations` 逐条记录，文件标 `unreadable`/`hash_skipped_oversize` |
| 坏 JSON / 坏编码 | 捕获并记录到 `errors`，继续处理其余材料，退出码仍为 0 |
| 启发式版本/别名分组 | `confidence=heuristic` + `uncertainty`；行政版本备注（draft `explicit`）可覆盖 |
| 冲突（同 stem 不同哈希且无版本标记） | 标 `source_conflict`，进 `pending_human`，不擅自裁定 |
| 输出目录不可写 | 降级到 `<work>/output_fallback`、再 `skill_output/`，并记 `degradations` |

**退出码恒为 0**：真实问题写进 `errors`/`degradations`，不靠退出码表达。

## 7. 脚本与文件

| 文件 | 作用 |
|---|---|
| `scripts/main.py` | 五命令入口（init/show/check/build/selfcheck）+ `run` 基线、契约回填、交付收口 |
| `scripts/libcore.py` | 确定性引擎：盘点、哈希、编码探测、版本/别名分组、索引、检索、校验 |
| `scripts/selftest.py` | 本地冒烟自检：临时夹具跑完整主路径 + 空/坏 JSON/无 draft/确定性 |
| `references/contract.md` | 字段级契约、locator 规范、状态语义 |

最小调用示例（**示意输出，非正式案例结果**）：

```bash
python scripts/main.py init
python scripts/main.py show
python scripts/main.py check
python scripts/main.py build
python scripts/main.py selfcheck
python scripts/selftest.py   # 作者本地自检（可选，运行时不调用）
```
