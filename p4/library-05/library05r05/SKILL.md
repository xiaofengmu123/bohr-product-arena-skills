---
name: library-doc-contract
description: 科研文档库修复与知识沉淀 Skill。以"输出契约逐字段对齐"为核心，对给定文件库完成确定性盘点、去重、版本/别名关系识别、元数据抽取、检索索引、可复跑交接清单与契约审计。适用"阅读批注与知识沉淀"赛道的真实文件库修复输入（文件、别名、行政版本备注、元数据、检索问题、恢复/索引操作）；先发现再假设、骨架优先、证据逐字溯源、缺料如实降级。
version: 2.0.0
---

# 科研文档库修复：契约逐字段对齐的盘点、版本/别名、检索与交接

## 1. 角色与何时使用

本 Skill 解决一类明确问题：科研资料散落在多个位置（多版本、重复下载、别名、临时笔记、元数据），
需要整理成"可检索、可交接、可复跑核对"的清单，并保留版本与别名关系，同时保证
`result.json` 与题目的 `output_contract` **逐字段对齐**。

**适用**：给定 `/input` 目录，盘点文件与权限、识别重复/版本/别名/冲突、抽取元数据、
建立检索索引、回答检索问题、记录跳过/失败/冲突与恢复方式，并交付逐字段契约审计。

**不适用**：文献检索与内容判断、学术写作、目录治理长期归档制度。

**关键判断**：实际输入画像是"真实文件库 + 别名 + 行政版本备注 + 元数据 + 检索问题 + 恢复/索引操作"，
故本 Skill 实现为确定性盘点引擎 + 输出契约审计，而不是 PDF 批注语义提取。PDF/扫描件的文本层
与 OCR 属外部工具职责，缺失时如实标注，不编造。

## 2. 输入契约

- 运行时输入在 `/input`：`case.json`（任务与 `output_contract`）、`task.md`，以及 `/input/` 下材料。
- 工作目录 `/app/work`，最终产物写 `/app/output/`。
- 六个子命令都支持 `--input/--work/--output` 覆盖默认目录；默认路径也必须能直接跑。
- **先发现，再假设**：不假设 `case.json` 的固定键名，不假设材料文件名；`output_contract`
  可能缺失，也可能为 dict/list（字符串或对象）两种形态，均需支持。
- 文本按 `utf-8-sig` 容错读取；坏 JSON、坏编码、不可读文件都要降级并记录，不崩溃。

## 3. 输出契约（本版重点：逐字段对齐 + change_log + contract_audit）

`/app/output/result.json` 必须包含：

- `case_id`：从输入原样带回，不允许改写；
- **`output_contract` 的全部字段**：dict/list 形态都支持，按原顺序逐键回填，缺值 `null`/`[]`，
  不改名、不删未知键、不合并键；
- 信封字段：`status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、
  `uncertainty`（对象）、`change_log`（操作记录，含 seq/scope/before/after/reason/locator）；
- 另附：`result`（盘点摘要与分组）、`claims`（逐条结论带证据）、`uncertainties`、
  `pending_human`、`errors`、`degradations`、`runtime`；
- **`contract_audit`（本版独有）**：逐字段审计 `{field, present, populated, type, source, change_seq}`，
  使"契约是否对齐"可被机器核对，而不是靠人工目测。

其他产物（可打开、可编辑、可复现）：

| 文件 | 内容 |
|---|---|
| `library_index.json` | 完整盘点：文件清单 + 分析（别名/版本/冲突/索引），确定性无时间戳 |
| `contract_report.json` | 逐字段契约审计报告 |
| `evidence_map.json` | 逐条 claim/source 的 `locator + 逐字 quote` 映射 |
| `inventory.md` | 可读交接清单 |

**溯源硬要求**：每条结论（重复、版本、别名、检索命中、跳过）都要能指回材料的逐字片段
（`source_id` + `locator` + `quote`）。冲突信息不隐藏：同一指标两个来源不一致时都记录并标
`source_conflict`。缺值写 `pending_human` 或 `not_reported`，不虚构引用、DOI、数值。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须落盘一版 `result.json` 骨架（`status=partial`），之后只做增量改进。**
接近时限立即 `build` 收口，绝不因追求完整而留下空产物。

| 步骤 | 输入 | 输出 | 时间预算 |
|---|---|---|---|
| 1. `python scripts/main.py init` | `/input` | 扫描盘点 → `work/library.json`、`work/contract_spec.json`、`work/draft.json` 模板、`output/result.json`（partial 骨架） | ≤60s，务必在前 3 分钟内 |
| 2. `python scripts/main.py show` | `work/library.json` | 打印带 locator 的文件/分组/问题/契约字段清单 | ≤30s |
| 3. 阅读材料 + 写 `work/draft.json` | show 输出与实际文件 | 模型只补充语义解释（行政版本备注、别名裁定、检索答案、修正启发式分组），每条带 evidence（逐字 quote） | 模型请求约 30–40 次 |
| 4. `python scripts/main.py check` | `work/draft.json` | `work/check_report.json`（结构 + source_id 存在 + quote 逐字命中） | ≤30s |
| 5. `python scripts/main.py build` | library + draft | `output/result.json` + 五个产物，无条件收口 | ≤60s |
| 6. `python scripts/main.py selfcheck` | `/app/output` | 复检 result.json 契约对齐与产物齐全 | ≤30s |
| 7. `python scripts/main.py contract` | `/app/output` | 打印逐字段契约审计（可选复核） | ≤15s |

**只运行 `build` 就结束、不读材料不写 draft，视为未完成。** `build` 的兜底只是防 0 分：
它会标 `status=partial` 并在 `degradations` 说明 `no_draft`，不可能拿完整分。

`work/draft.json` 结构（模板见 `scripts/main.py` 的 `DRAFT_TEMPLATE`）：

```json
{
  "case_id": "…",
  "reviewed": true,
  "notes": [{"target": "F004", "kind": "alias|version|metadata|correction",
             "text": "…", "basis": "…", "confidence": "explicit|inferred|heuristic",
             "evidence": [{"source_id": "F004", "locator": "materials/x.pdf", "quote": "逐字片段"}]}],
  "queries": [{"query_id": "Q01", "answer": "…", "status": "answered|no_direct_match|insufficient_evidence",
               "evidence": [{"source_id": "F004", "locator": "…", "quote": "…"}]}]
}
```

写完 draft 后把 `reviewed` 置为 `true`，表示已阅读实际材料；`notes`/`queries` 可为空数组。

## 5. 自检清单（build 后逐项核对，对应评分维度）

- [ ] **实际效果·完成度**：`result.json` 可解析；`case_id` 与输入一致；`contract_audit.total_fields == 契约字段数` 且每个字段 `present=true`。
- [ ] **实际效果·正确性**：每条 claim 的 `quote` 在材料中逐字命中；定位用 `rel_path + sha256`，不张冠李戴。
- [ ] **实际效果·完整性**：覆盖各类文件类型、元数据字段、目录层级、重复、版本差异、权限与来源。
- [ ] **实际效果·可核验性**：`evidence_map.json` 含 locator + quote；同输入两次运行除 `runtime` 外逐字节一致。
- [ ] **使用增益**：`init` 一步完成盘点，人工返工少；`check` 拦截编造与错源；`contract` 一眼看契约缺口。
- [ ] **可靠体验**：六个子命令均退出码 0；空输入/坏 JSON/缺目录/无文本层/损坏/权限不足均降级不崩溃。
- [ ] **生态贡献**：SKILL.md 说明齐全；脚本纯标准库、相对路径、可独立复跑；`selftest.py` 离线可验。

## 6. 降级策略（缺数据/缺软件必须如实标注，不允许编造）

| 场景 | 处置 |
|---|---|
| 材料不足 / 无文本层 | `status=insufficient_evidence`，缺口写 `pending_human` |
| 缺外部工具（pdftotext/OCR/office） | 只做字节级盘点，`degradations` 标 `no_tool:<name>`，不假装有文本层 |
| 加密/损坏/权限不足/超大文件 | `errors`/`degradations` 逐条记录，文件标 `unreadable`/`hash_skipped_oversize` |
| 坏 JSON / 坏编码 | 捕获并记录到 `errors`，继续处理其余材料，退出码仍为 0 |
| 启发式版本/别名分组 | `confidence=heuristic` + `uncertainty`；行政版本备注（`explicit`）可覆盖 |
| 冲突（同 stem 不同哈希且无版本标记） | 标 `source_conflict`，进 `pending_human`，不擅自裁定 |
| `output_contract` 缺失 | 使用默认契约并标 `contract_fallback`，逐字段审计标注来源 |
| 输出目录不可写 | 降级到 `<work>/output_fallback`、再 `skill_output/`，并记 `degradations` |

**退出码恒为 0**：真实问题写进 `errors`/`degradations`，不靠退出码表达。所有状态与缺口必须自洽。

## 7. 脚本与文件

| 文件 | 作用 |
|---|---|
| `scripts/main.py` | 六命令入口（init/show/check/build/selfcheck/contract）、契约回填、交付收口 |
| `scripts/libcore.py` | 确定性引擎：盘点、哈希、类型/编码探测、版本/别名分组、索引、检索、契约发现/校验 |
| `scripts/selftest.py` | 离线冒烟自检（不依赖正式案例，用临时合成夹具） |
| `references/contract.md` | 字段级契约、定位规范、状态语义 |
| `references/checklist.md` | 本地冒烟协议与核对清单 |

最小调用示例（**示意输出，非正式案例结果**）：

```bash
python scripts/main.py init      # 扫描 /input，写 partial result.json 骨架
python scripts/main.py show      # 读带 locator 的盘点，再写 /app/work/draft.json
python scripts/main.py check     # 机械校验 draft
python scripts/main.py build     # 收口：result.json + library_index.json + inventory.md 等
python scripts/main.py selfcheck # 复检产物与契约对齐
python scripts/main.py contract  # 打印逐字段契约审计
```
