---
name: research-translation-writing
description: 科研成果转化写作。把论文摘要、技术报告、临床/实验底稿等科研材料改写成指定受众与体裁（科普、项目申报、简报、双语摘要、新闻稿等）的可复核改稿；锁定术语、保留事实数据与引用、按源文证据强度定调，并输出逐段对照与改动清单。当任务要求“依据固定科研材料产出可编辑写作产物且必须保留来源与边界”时使用。
version: 1.0.0
---

# 科研成果转化写作 Skill

## 1. 角色与适用时机

本 Skill 是“科研成果转化写作”执行器：接收科研事实底稿与目标读者/体裁/篇幅约束，产出**可直接使用的完整改稿**（不是建议、提纲或片段润色），并同步交付证据映射与改动清单，使原作者与第三方能逐段复核。

适用：论文摘要→科普、技术报告→项目申报、科研底稿→简报/双语摘要/新闻稿，以及校对、结构化、格式与引用规范化。不适用：无源材料的纯问答、一次性脚本、只给写作建议而不产出成品。

## 2. 输入契约

- 运行时原始输入固定位于 `/input/case.json` 与 `/input/task.md`；科研材料通常在 `/input/materials/` 或 `/input/` 下的 `*.pdf`、`*.txt`、`*.md`、`*.csv`、`*.json`。
- **先发现，再假设**：扫描 `/input` 目录，不假设固定文件名或键名；`case.json` 可能携带 `output_contract`，其全部键必须原样回填。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级不崩溃）；单文件超过 2MB 跳过并记入 `degradations`。
- PDF 用环境自带 `pdftotext` 转文本后再消费；读不到 PDF 正文时如实记录，不依据文件名/标签猜内容。
- 中间产物写 `/app/work/`，最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，并同时给出可编辑主产物（默认 `deliverable.md`；若题目要求 Office/图，则同时给出可编辑源文件与真实 PDF/PNG 预览）。

`result.json` 必需字段：

| 字段 | 说明 |
|---|---|
| `case_id` | 从 `case.json` 取，缺失则用输入目录名，禁止留空 |
| `status` | `ok / partial / insufficient_evidence / input_error` |
| `deliverable` | 主产物相对 `/app/output` 的路径 |
| `evidence_map` | 关键数字/术语/引用/图表/论断，每项含 `locator` + 逐字 `quote` + `type` |
| `terminology` | 术语锁定表（canonical/abbr/expansion/variants/first_use） |
| `change_log` | 每处删改含 `locator/before/after/reason` |
| `sources` | 实际读到的源文件清单 |
| `uncertainty` / `pending_human` / `errors` / `degradations` / `limitations` | 边界与异常 |
| `runtime` | `mode` 与耗时 |
| `output_contract` 的全部字段 | 原样回填，缺值给 `null`/`[]`，不改名、不删未知键 |

`status` 诚实赋值：材料不足即 `insufficient_evidence`，解析失败即 `input_error`；不编造数据或结论。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**Phase 0 — 落骨架（0–3 分钟，必须）**
先执行 `python3 -B scripts/main.py --mode skeleton`，立即写出合法 `result.json`（`status=partial`）与 `deliverable.md` 结构骨架。此后任何中断都不会留下空产物。
输入：`/input`。输出：`/app/output/result.json`、`/app/output/deliverable.md`、`/app/work/facts.json`。

**Phase 1 — 读材料建事实底稿（3–10 分钟）**
完整读 `case.json`、`task.md` 与全部材料；确认受众、体裁、篇幅上限；用 `facts.json` 核对数字、样本量、单位、指标口径、图表编号、引用；标记材料缺口与冲突。

**Phase 2 — 写改稿（10–14 分钟）**
按“受众画像→取舍策略→边界标注→多文体映射”（`references/genre-mapping.md`）完成整篇改稿，写入 `deliverable.md`；锁定术语；保留必须项，删除仅限冗余；每处删改记入 `change_log`。

**Phase 3 — 复核与终稿（14–15 分钟）**
执行 `python3 -B scripts/check.py --output /app/output` 与 `python3 -B scripts/main.py --mode finalize`；把骨架更新为 `status=ok/partial/insufficient_evidence`，回填 `uncertainty/pending_human/limitations`，确认每个数字/引用/论断可回溯源文。

## 5. 核心方法（四步链 + 证据约束）

1. **受众画像**：先写读者背景、阅读目的、可接受术语密度、篇幅与格式，再决定解释深度、类比与取舍。
2. **取舍策略**：必须保留=关键结论、数值与单位、样本量与口径、图表编号、引用、限定条件；可压缩=方法细节与推导；可删除=冗余与无关修辞。压缩优先保证据而非文采。
3. **边界标注**：相关≠因果、初步≠定论；源文“suggest/提示/初步”不得升级为“证实/导致”；限定语逐句对齐；源文未支持的新结论一律删除或转 `pending_human`。
4. **多文体映射**：按 `references/genre-mapping.md` 的体裁结构表补齐必备要素，保持结构要素与论断强度对应。
5. **证据映射**：每个关键数值/术语/引用/图表/论断回指源文 `locator + quote`。
6. **改动清单**：每处删改记录 `before/after/reason`，支持逐段复核与继续编辑。

## 6. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] 产出了**完整改稿**而非建议/提纲（任务完成度）
- [ ] 数字、单位、口径、样本量、图表编号、引用与源文一致，无新增结论（结果正确性）
- [ ] 关键结论、数据、限定条件、方法要点、图表引用全部保留，删的只是冗余（完整性）
- [ ] 每处删改有 `change_log`（before/after/reason），可逐段复核（可核验性）
- [ ] `result.json` 含 `case_id`、`status`、`sources`、`uncertainty`、`change_log` 及 `output_contract` 全字段
- [ ] 空输入/坏 JSON/坏编码三种异常各跑一次不崩溃，状态诚实（可靠体验）
- [ ] 同一输入重复运行结果一致（确定性）；脚本相对路径可执行
- [ ] 体裁模板、术语表、取舍清单、验收口径已沉淀到 `references/`（生态贡献）

## 7. 降级与诚实标注

- 缺源文/缺关键数据：`insufficient_evidence`，列 `pending_human`，不补造。
- 源文数据自相矛盾：两处都记录并标 `source_conflict`，不改写为单一事实。
- 体裁与篇幅冲突、材料涉密或未发表：写入 `limitations`，先给合规最小产物。
- 缺软件/PDF 解析失败：记 `degradations`，继续处理其余材料，绝不伪造运行状态或数值。
- 同一策略失败两次即停止重复，改走降级路径并如实标注。

## 8. 脚本与调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --mode skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py --mode finalize --input /input --output /app/output --work /app/work
python3 -B scripts/check.py --output /app/output
```

脚本缺省参数即指向 `/input`、`/app/output`、`/app/work`。
