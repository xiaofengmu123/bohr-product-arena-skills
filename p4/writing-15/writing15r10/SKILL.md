---
name: thesis-longdoc-writing
description: 面向学位论文与长文档写作的可复核 Skill：章节规划、跨章节一致性、局部改写与证据溯源。读取 /input 的 case.json、task.md、科研材料与 organizer-edit-workorder.json，先落盘可复核的 result.json 骨架，再增量产出可编辑稿件、证据映射、术语/引用表与改动记录到 /app/output/。当需要依据固定科研材料完成章节生成、改写、校对、格式统一或引用核对时使用。
version: 1.0.0
---

# 学位论文与长文档写作 Skill

## 1. 角色与何时使用

本 Skill 把"材料 → 长文档写作/改写/校对"沉淀为可复核、可继续编辑、可独立验证的工作流，聚焦四个问题：**立得住**（章节规划对应提纲与论证链条，而非堆砌文字）、**连得上**（跨章节术语/定义/符号/编号/图表引用前后一致）、**改得动**（局部修改只动该动处、保留作者原意）、**查得清**（每条论断、引用、改动可追溯到原文/文献/版本记录）。

**使用时机**：运行契约在 `/input` 提供 `case.json` 与 `task.md`，要求依据固定科研材料产出学位论文/长文档的章节、改写、校对、格式统一、术语与引用核对、双语或结构化写作产物。**不适用**：无输入材料的纯问答、一次性脚本、预制答案。

**分工**：文字与论证由模型产出；术语变体、编号/交叉引用、引用缺口、证据逐字定位由脚本机械核查，使结果可独立复核、重复运行一致。

## 2. 输入契约（先发现，再假设）

运行时原始输入固定位于 `/input/`：

- `case.json`（必需）：任务说明、`case_id` 与 `output_contract`（结果必须回填的字段）。
- `task.md`（必需）：题目约束、体裁、受众、格式要求、`challenge_id`。
- 科研材料：任意命名的 `.md/.txt/.tex/.pdf/.docx/.pptx/.xlsx`；可能是"分节的、可编辑的工作章节"（organizer 输入修复说明），并可能附带 `organizer-edit-workorder.json` 列出编辑目标与离线工作流范围。
- 环境清单：`environment/resources.yaml` 列出预装工具（pdf-text-tool、pdf-render-tool、ocr-tool、office-renderer、latex-compiler、browser-automation、font-discovery 等）。

**发现而非假设**：不假定固定文件名/键名，用 `scripts/main.py --mode run` 自动发现；对 BOM、坏编码、坏 JSON、超 2MB 文件降级处理并记入 `degradations`。实际输入文件与原文是唯一事实依据，不按错误的领域标签补造数据。中间文件写 `/app/work`，最终产物写 `/app/output/`。

## 3. 输出契约（必须产出 /app/output/result.json）

`result.json` 必须包含：

- `case_id`：优先取 case.json 的 `case_id`/`id`，其次 task.md 的 `challenge_id`，最后输入目录名。
- **题目 `output_contract` 的每一个字段**：原样回填到顶层，缺值先给 `null`/`[]`，不改名、不删未知键。
- 固定信封：`status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、`uncertainty`、`change_log`。
- 辅助字段：`artifacts`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`、`output_contract`（契约说明）。

其余产物需"可打开、可编辑、可复现"：

- 稿件/改稿：`.md`（主）或 `.docx`；修订与原文的差异成 `change_log`。
- 证据映射：每项 `{claim, locator, quote}`，quote 为材料逐字片段。
- 术语/符号/编号表、引用清单：`.md` 或 `.json`。
- Office/图表产物附真实渲染的 PDF/PNG 预览（由预装 office-renderer/pdf-render-tool 生成）。

`status` 必须诚实：材料不足给 `insufficient_evidence`；禁止编造数值、引用、DOI、评测分数。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**铁律：前 3 分钟内必须先落盘一版 `result.json` 骨架**，此后只做增量改进，保证任何超时/中断都留下合法结果。

| 步骤 | 输入 | 输出 | 建议时间 |
|---|---|---|---|
| 1 发现输入 | /input 目录 | 文件分类清单 | ≤30 s |
| 2 写骨架 | case.json/task.md | result.json（status=partial，契约字段=null） | ≤30 s |
| 3 读材料 | materials/** | 材料文本 + 编辑目标 | ≤90 s |
| 4 规划 | 提纲/任务 | 章节规划、术语表、待改写项清单 | 按需 |
| 5 写作/改写 | 材料 + 规划 | 各章稿件（.md） | 核心阶段 |
| 6 机械检查 | 稿件 + 材料 | check_report.json | ≤60 s |
| 7 回填证据 | 稿件 + 材料 | evidence_map、term_table | ≤60 s |
| 8 定稿 | 全部 | result.json（status 更新）+ 预览 | ≤90 s |

**步骤细节**：

1. 立即运行 `python3 -B scripts/main.py --mode skeleton`，得到合法骨架（骨架即 `--mode run` 的第一步）。
2. 骨架落盘后读材料全文：PDF 用预装 pdf-text-tool 或脚本的 pdftotext 兜底；Office 用 office-renderer 提取文本。
3. 依任务产出：章节规划按论证链条排顺序/篇幅/证据；改写保留原意、只动必要处；校对统一术语/编号/引用。
4. 每改一处写 `change_log` 项 `{time, action, before, after, reason}`；每个论断/数值/引用回填 `evidence_map` 项 `{claim, locator, quote}`。
5. 运行 `python3 -B scripts/main.py --mode check` 跑术语变体、编号/交叉引用、引用缺口检查；据 report 修正稿件后重跑。
6. 运行 `python3 -B scripts/main.py --mode finalize` 校验 result.json、补齐契约字段、记录 runtime。

**请求预算**（64 次、16384 token/响应）：材料粗读 ≤4 次；分章写作每次 1 章、≤30 次；一致性修正 ≤10 次；证据回填与定稿 ≤10 次；脚本调用不占模型请求。超长材料按章分次读，单次不塞全文，避免截断。

## 5. 自检清单（逐条对应评分维度）

- [ ] 实际效果：稿件是可用的章节/改写，而非通用建议；每条论断与所引文献/材料对得上。
- [ ] 实际效果：数据、术语、符号、章节与图表编号与原文/给定材料一致，无新增材料外事实。
- [ ] 实际效果：提纲要求的章节、研究问题/方法/结果/讨论/局限、图表、参考文献、附录齐全。
- [ ] 实际效果：章节文字可追溯到 outline 条目、原始材料、具体文献、版本记录（evidence_map 非空、quote 逐字）。
- [ ] 使用增益：术语/编号/引用由脚本统一，减少人工通读与逐条补引用；跨章节同概念同写法。
- [ ] 使用增益：局部修改只动必要处，change_log 说清改了什么、为什么改，可回退、可比较。
- [ ] 创新增益：术语变体检测、编号/交叉引用检查、证据定位由脚本独立完成，可脱离模型运行。
- [ ] 可靠体验：`--help` 退出码 0；空目录/坏 JSON/缺输入三场景各跑一遍不崩溃且 status 诚实。
- [ ] 可靠体验：同一输入重复运行结果一致（脚本确定性）；不同章节类型与篇幅均产出可用文本。
- [ ] 生态贡献：SKILL.md、脚本、references 模板与清单可被他人直接复用；限制、篇幅上限、失败模式已写明。

## 6. 降级策略（禁止编造）

- **缺材料**：status=`insufficient_evidence`，`limitations` 列出缺口，`pending_human` 列需人工提供项，不填充示例内容。
- **坏 JSON/坏编码/超长**：记入 `degradations`，继续处理其余材料；单文件 >2MB 跳过。
- **引用找不到原文**：evidence_map 标 `not_found`，写入 `uncertainty`；不伪造 locator/quote。
- **结论冲突/编号不连续**：记 `source_conflict`/编号 gap 到 `uncertainty` 与 check_report，两处都保留，不静默取舍。
- **缺软件（pdftotext/office-renderer 等）**：`degradations` 记录 `tool_missing:xxx`，退化为纯文本，仍产出合法 result.json。
- **格式不支持/超长截断**：明确标注截断位置与处理方式，写进 `limitations`。
- **写盘失败**：降级写 `/app/work/result.json.fallback`，进程退出码仍为 0。

## 7. 脚本调用（相对路径，仅标准库）

```bash
# 骨架（第一步必跑）
python3 -B scripts/main.py --mode skeleton --input /input --output /app/output --work /app/work
# 机械检查
python3 -B scripts/main.py --mode check    --input /input --output /app/output --work /app/work
# 定稿校验
python3 -B scripts/main.py --mode finalize --input /input --output /app/output --work /app/work
# 一键（先骨架、后检查、再定稿）
python3 -B scripts/main.py --mode run      --input /input --output /app/output --work /app/work
```

缺省 `--input /input`、`--output /app/output`、`--work /app/work`。脚本确定、可重复运行，只用标准库。

## 8. 边界

一个 Skill 只解决"长文档写作/改写 + 一致性 + 证据溯源"一类任务，不追求通用大而全。无真实材料时输出"材料不足"而非示例填充。交付前在无作者环境（不同机器/Python 版本）各跑一次空目录、坏 JSON、缺输入三场景。
