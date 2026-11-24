---
name: thesis-longdoc-editing
description: 为学位论文与长文档的改写、一致性检查与版本记录提供可复核工作流：发现材料、解析编辑工单、抽取术语/符号/编号/引用、产出可编辑稿件与 change_log/evidence_map、诚实标注缺口。当需要围绕给定科研材料完成章节改写、术语统一、引用核对、编号重排或评审意见落地时使用；不用于从零生成虚构论文，也不用于纯聊天问答。
version: 1.0.0
---

# 学位论文与长文档写作 Skill

## 1. 角色与何时使用

本 Skill 把“长文档编辑与写作”沉淀为可被 Agent 调用、可被他人复核的流程。核心不是写得长，而是四个可验证目标：

- **立得住**：按提纲/工单写作，而不是把材料堆成若干段通顺文字。
- **连得上**：术语、符号、定义、编号、图表与交叉引用前后一致。
- **改得动**：局部修改只动需要动的地方，保留作者原意，可说明改了什么、为什么改。
- **查得清**：每个论断、每条引用、每次改动都能回溯到原文/文献/版本记录。

**何时使用**：输入包含科研事实底稿 + 任务说明（`case.json`/`task.md`）或编辑工单（`organizer-edit-workorder.json`），需要产出可编辑稿件/改稿/引用结果时。**不适用**：无真实材料的一次性问答；需要用预写答案代替实际运行。

## 2. 输入契约

运行时固定输入位于 `/input/`，工作目录 `/app/work/`，产物写 `/app/output/`。

- 必读 `/input/case.json`：含 `case_id`、任务描述、可能的 `output_contract` 字段与约束。
- 必读 `/input/task.md`：任务与评分说明（若存在）。
- 优先读 `/input/organizer-edit-workorder.json`：明确“编辑目标”与离线本地工作流范围（本题关键，多个案例据此给出逐条改稿目标）。
- 科研材料按原题提供，如 `materials/`、章节化可编辑原稿（W16 为分节可编辑章节而非成品论文）。**以实际文件内容为事实依据，不依据文件名/领域标签补造数据**。
- PDF 是实际 PDF，不带预提取答案层；用平台提供的 PDF 解析工具（pdf-text-tool）读取，不得凭标签臆测内容。

**发现规则（先发现，再假设）**：不假设固定文件名/键名；用 `scripts/main.py scaffold` 扫描。文本按 `utf-8-sig` 等编码容错读取；单文件超 2MB 跳过并记 `degradations`；坏 JSON 捕获后降级并继续。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段如下：

- `case_id`：与输入一致。
- `status`：`ok | partial | insufficient_evidence | input_error`（诚实取值，见降级策略）。
- **题目 `output_contract` 的全部字段**：原样回填，键名不改、不删未知键；值缺则 `null`/`[]`。
- `sources`：证据清单，每项含 `locator`（如 `materials/x.md#L12`）+ 逐字 `quote`。
- `uncertainty`：不确定项与依据。
- `change_log`：每次改动的 `{id, target, locator, before, after, reason, source}`。
- 补充：`errors`、`degradations`、`pending_human`、`runtime`（记录真实耗时，不伪造）。

**主产物**：可打开、可编辑、可复现。文本类给 `.md`/`.json`；图形给可编辑 SVG + 真实 PDF/PNG 预览；Office 给 `.docx/.pptx/.xlsx` 并附渲染预览。稿件正文写进 `/app/output/` 下分节 `.md` 文件，`result.json` 中给出路径。

## 4. 执行流程与时间预算（总 900s / 64 次模型请求）

> 核心纪律：**前 3 分钟内先落盘一版 result.json 骨架**，之后只做增量改进，任何中途失败都不留下不完整产物。

| 阶段 | 动作（输入→输出） | 预算 |
|---|---|---|
| 0. 骨架落盘（0–3 min） | `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`。扫描输入、解析 case/task/工单、抽取事实、跑一致性检查；写 `result.json`（`status=partial`）、`state.json`、`facts.json`、`consistency_report.md` | ≤3 请求 |
| 1. 精读与规划（3–20 min） | 读工单每一条 → 定位目标章节/段落 → 写 `/app/work/edit_plan.md`（条目→节→改什么→约束→证据） | ≤15 请求 |
| 2. 执行改写（20–45 min） | 逐条最小化改写，保留作者原意与原句；每条写 `change_log`；正文写 `/app/output/<章节>.md` | ≤30 请求 |
| 3. 一致性自检（45–55 min） | `python3 -B scripts/main.py check ...`；按报告修复编号/术语/引用/交叉引用 | ≤10 请求 |
| 4. 收尾（55–60 min） | 写 `/app/work/deliverables_map.json` 与 `/app/work/change_log.json`；`python3 -B scripts/main.py finalize ...` 汇总 `result.json` | ≤6 请求 |

重复运行需幂等：随机项固定种子；`scaffold` 可安全重跑，`finalize` 只合并不覆盖正文。

## 5. 自检清单（对应评分维度）

- [ ] 实际效果：正文按提纲/工单真正写成了可用章节，而非给通用建议（完成度 15 / 正确性 15）。
- [ ] 每条论断、数字、术语、符号、图表编号与原文一致，引用能对上（正确性 15）。
- [ ] 研究问题/方法/结果/讨论/局限与图表、参考文献、附录齐全（完整性 5）。
- [ ] 正文可逐条回溯到大纲条目、原始材料、文献、版本记录（可核验性 5）。
- [ ] 使用增益：术语/编号/交叉引用一次到位，减少通读与手工统一（质量/效率增益）。
- [ ] 创新增益：局部最小改写 + 可回退 change_log + 一致性检查，优于裸 Agent 与通用模板。
- [ ] 可靠体验：一次“给定材料→生成/改写→检查一致性”可复现；异常场景有处置。
- [ ] 生态贡献：说明、依赖、适用范围、篇幅上限、失败模式齐全，脚本与清单可直接复用。

## 6. 降级策略

- **缺数据/材料**：`status=insufficient_evidence`，`pending_human` 列出缺口，**不得编造数据或结论**。
- **坏 JSON/坏编码/超长截断**：捕获记入 `errors`/`degradations`，继续处理其余材料；`status=partial`。
- **PDF 无解析层**：用平台 pdf-text-tool；仍失败则标记该材料未纳入，写 `limitations`。
- **缺软件（Office/LaTeX/渲染）**：改交 `.md`+`.json`，在 `degradations` 说明缺少的预览，不伪造渲染状态。
- **冲突信息**：同指标两来源不一致时，两个都记录并标 `source_conflict`，不擅自取舍。
- **写盘失败**：降级写最小合法 `result.json`，不崩溃；缺失值一律 `null`/`[]`。
- 状态词必须诚实：`ok` 仅在主产物与证据齐备时使用；有缺口用 `partial`；无材料用 `insufficient_evidence`。

## 7. 脚本与资源（相对路径调用）

- `scripts/main.py`：`scaffold | check | finalize` 三子命令与全流程编排。
- `scripts/discover.py`：输入发现与容错解析（被 main 导入）。
- `scripts/checks.py`：术语/编号/引用/交叉引用/冲突启发式检查。
- `references/consistency-checklist.md`：逐项检查清单与评分维度映射。
- `references/failure-modes.md`：已知失败模式与处置。

仅依赖 Python 标准库；调用一律 `python3 -B scripts/main.py <subcommand> --input /input --output /app/output --work /app/work`。缺省参数即 `/input`、`/app/output`、`/app/work`。

## 8. 最小调用示例（示意，非正式结果）

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 读 /app/work/facts.json 与 edit_plan，写正文与 change_log
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```

示意 `result.json`（标注为示例，不冒充正式结果）：

```json
{
  "case_id": "EXAMPLE-ONLY",
  "status": "partial",
  "main_artifact": "output/chapter-3.md",
  "sources": [{"locator": "materials/paper.md#L12", "quote": "……"}],
  "uncertainty": [{"item": "示例缺口", "basis": "未提供原始数据"}],
  "change_log": [{"id": "e1", "target": "3.2", "before": "……", "after": "……", "reason": "……", "source": "……"}]
}
```
