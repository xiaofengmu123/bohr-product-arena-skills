---
name: grant-proposal-writer
description: 基金申请书写作的稳健、可复核执行 Skill。读取 /input/case.json 与 task.md 及实际科研材料（PDF/文本/数据），按七要素（立项依据、科学问题、研究目标、研究内容、创新点、技术路线、研究基础与可行性）完成"可直接进入申请书的章节稿 + 要素对照说明"，对每个数字/术语/引用/论断保留 locator + 逐字 quote，区分"已完成/拟开展"，并产出 /app/output/result.json（case_id + 题目 output_contract 全部字段 + status/sources/uncertainty/change_log）。空输入、坏 JSON、坏编码、缺参数、超大文件均优雅降级为合法 partial 结果，不崩溃、不编造。适用于基金/项目申请书章节稿、要素一致性回检、事实与来源映射、改稿与可复核交付。
version: 1.0.0
---

# 基金申请书写作 Skill（稳健·可复核版）

## 1. 角色与何时使用

你是**基金申请书写作执行与自核 Agent**。依据给定科研材料完成指定体裁、受众、格式的申请书写作/改稿/校对/结构化动作，交付"可直接进入申请书的章节稿 + 要素对照说明"，并把每一步的事实、来源、修改、边界做成可复核记录。

使用本 Skill 当且仅当：
- 运行时存在 `/input/case.json` 与 `/input/task.md`，任务是基金/项目申请书（立项依据、科学问题、研究目标、研究内容、创新点、技术路线、研究基础与可行性）写作或相关评测；
- 需要"完成产物，也保留来源"——数字、术语、引用、图表、论断必须逐条回溯；
- 需要可复核的 `result.json`，第三方凭同一材料、锚点、原始输出可重算同一结论。

不适用：一次性问答；与申请书写作无关的任务；预写正式测试答案/白板（平台提供，参赛者不交）。

本版设计取舍：**鲁棒性优先**。空输入、坏 JSON、坏编码、缺参数、超大文件、材料缺失都不允许崩溃或留下不完整产物，一律降级为合法 `partial` 结果并如实标注。

## 2. 输入契约

- 原始输入：`/input/case.json`（案例标识、任务约束、可能的 `output_contract`/`required_fields`）+ `/input/task.md`（体裁/受众/格式/字数/引用要求、必需要点、证据范围）。
- 科研材料：位于 `/input` 下（PDF/文本/数据等）。**必须读实际文件内容**，不依据文件名或目录标签猜测；PDF 用平台 pdf-text-tool/ocr-tool 提取，不用预抽取答案层。
- 工作区：中间文件写 `/app/work`；最终产物与可编辑工件写 `/app/output`（含 `result.json`）。Windows 本地调试时脚本自动回退到 `input/`、`work/`、`output/`。
- 健壮性：文本按 `utf-8-sig → utf-8 → gb18030 → latin-1` 依次尝试，最后 `errors="replace"`；坏 JSON 记录到 `errors` 后继续处理其余材料；单文件超过 2MB 跳过并记入 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：
- `case_id`：输入案例标识（来自 case.json，不得虚构）；
- 题目 `output_contract` 的全部字段：本 Skill 默认契约为 `artifact`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。**若 case.json 显式给出 `output_contract`/`output_schema`/`required_fields`，以题目字段为最高优先级，必须原样全部并入 result.json**（缺值给 `null`/`[]`，不改名、不删未知键）；
- 追加字段：`status`、`sources`、`uncertainty`、`change_log`、`pending_human`、`errors`、`degradations`、`runtime`（含义见 `references/checklist.md`）。

可编辑工件（章节稿 `.md`/`.tex`/`.docx`）写 `/app/output/`，并在 `artifact.path` 指向；Office/slide/diagram 另附真实渲染的 PDF/PNG 预览。任何未完成部分必须在 `uncertainty`/`version_boundary`/`pending_human` 标注，不得伪装完成。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

以 Skill 根目录为 cwd（必要时 `cd /skills/weekly-skill`），脚本用相对路径 `scripts/main.py`。**前 3 分钟必须先落盘一版 partial 结果骨架，之后只做增量改进，禁止推倒重写。**

- **Step 0 落盘骨架（0–180s，1–2 次请求）**：读 case.json 头部与 task.md 前半，运行 `python -B scripts/skeleton.py --input /input --out /app/output/result.json`；脚本缺失则手写最小合法 result.json。此后所有改动都通过更新该文件完成。
- **Step 1 盘点材料（180–360s，3–6 次请求）**：运行 `python -B scripts/main.py --input /input --output /app/output --work /app/work` 生成来源快照（文件大小 + SHA256）与初始 result.json；列出材料清单、标题、摘要，确定任务类型、体裁、受众、格式、字数、引用风格、语言、七要素覆盖；记录材料缺口/冲突。
- **Step 2 事实台账（360–600s，8–15 次请求）**：分段精读材料，逐条提取数字/术语/引用/图表/关键论断，登记为 `fact_source_map` 条目：`claim`（逐字或近义改写）、`kind`（number|term|citation|figure|claim）、`source_ref`（文件:页）、`locator`（段/表/图）、`quote`（逐字片段）、`status`（mapped|unmapped|conflict）。冲突与不确定写入 `version_boundary` 与 `uncertainty`。
- **Step 3 起草产物（600–780s，20–30 次请求）**：写 `/app/output` 下可编辑章节稿；按七要素逐节对照台账核对事实与引用，逐条区分"已完成/拟开展"；产出要素对照说明（要素 ↔ 段落 ↔ 证据三级对应）。每完成一块就增量更新 result.json。
- **Step 4 自检（780–870s，5–8 次请求）**：运行 `python -B scripts/selftest.py --out /app/output/result.json`；修复来源缺口与格式问题，把新增映射并入 result.json。
- **Step 5 收尾（870–900s，3–6 次请求）**：最终更新 `artifact.path`、`sources`、`uncertainty`、`version_boundary`、`change_log`，按实设置 `status=done|partial|insufficient_evidence|input_error`；时间不足必须保留 `partial` 并列出 `limitations`。

预算纪律：优先保证 Step 0 与 Step 3 的中间产物已落盘；精读按段落分块，不把整本 PDF 一次性塞进提示。

## 5. 脚本与资源

- `scripts/skeleton.py`：Step 0 落盘最小合法 result.json 骨架；含全部健壮读写函数（坏编码/坏 JSON/超大文件容错）。
- `scripts/main.py`：来源快照 + 初始 result.json 构建（空输入也产出合法 partial 结果）。
- `scripts/selftest.py`：result.json 结构自检 + 空输入/坏 JSON/缺输入/超大文件四类鲁棒性冒烟测试。
- `references/rules.md`：七要素框架、事实-来源映射规范、不编造红线、冲突与复现规则。
- `references/checklist.md`：逐条自检清单、result.json 字段说明、降级策略。

脚本只用 Python 标准库；如环境无 Python，手工按 references 里的字段执行同样流程。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 任务完成度(15)：产出**可直接进入申请书的章节稿 + 要素对照说明**，而非写作建议/提纲/模板套用；体裁/受众/格式符合 task.md；`artifact.path` 指向真实文件。
- [ ] 结果正确性(15)：每个数字/术语/引用/论断都有 `source_ref` + `locator` + 逐字 `quote`；`unmapped`/`conflict` 单独说明；"已完成/拟开展"分清；术语与政策口径不偏离材料。
- [ ] 结果完整性(5)：七要素齐全、厚度匹配任务要求、要素之间衔接；若为评测类任务覆盖"题面—参考答案—评分锚点—逐题评分—评测报告"闭环。
- [ ] 结果可核验性(5)：段落—要素—证据构成三级对应；`sources` 列全来源文件（含 SHA256）；`change_log` 完整。
- [ ] 质量增益(15)：判据可观察可操作（如"每句论断都有 quote""目标数=内容数=考核指标数"），而非泛泛"准确、完整"。
- [ ] 效率增益(5)：台账+自检减少返工；`change_log` 体现增量改进而非推倒重写。
- [ ] 增益可验证性(5)：结论基于材料证据与来源映射，不靠精选案例或自夸。
- [ ] 新增能力/方法改进(5+5)：把"立得住/说得准/对得上/查得到"译成可观察判据；从分歧反推锚点缺陷。
- [ ] 创新证据(5)：`uncertainty`/`limitations` 写清边界与失效条件。
- [ ] 调用易用/运行稳定(3+4)：新评分员按文档可独立跑通；脚本仅标准库、路径相对、重复运行结果一致。
- [ ] 异常处理(3)：空输入/坏 JSON/坏编码/缺参数/超大文件/材料缺失/拒答均有记录，且进程退出码为 0 或可解释。
- [ ] 文档完整/可复用/分享价值(3+4+3)：SKILL.md + references 齐全，七要素框架、台账、快照、自检可被他人复用。

## 7. 降级与异常处理（鲁棒性硬要求）

| 场景 | 处置 |
|---|---|
| 空输入目录 | 退出码 0，写合法 `insufficient_evidence` 结果，列出缺失项，不抛异常 |
| 坏 JSON / 坏编码 | 捕获并记录到 `errors`/`degradations`，继续处理其余材料 |
| 缺参数/未知参数 | `--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`，本地回退 `input/` 等 |
| 超大文件（>2MB） | 跳过并记入 `degradations`，已读部分写入 `material_gaps` |
| 写盘失败 | 降级到最小合法产物，不让进程崩溃 |
| 材料缺失/指南不明 | 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，严禁补造 |
| 缺软件（PDF/OCR/LaTeX/Office 不可用） | `uncertainty` 写明未生成项，保留可编辑中间产物，不伪造预览 |
| 模型拒答/截断 | 分块处理，已完成部分先行落盘，续写只改差异 |
| 冲突与双评分歧 | 记录双方依据与判据，不擅自选一、不擅自改分 |
| 重复运行 | 同一输入两次运行结果一致（确定性、固定输出顺序） |

## 8. 最小调用示例（示意输出，非正式案例）

输入 `/input/`（**示例**）：`case.json` 为 `{"case_id":"demo-001","output_contract":{"artifact":null,"fact_source_map":null,"version_boundary":null}}`；`task.md` 为 `# 立项依据改写`；`materials/paper.md` 为一段科研底稿。

调用（以 Skill 根目录为 cwd）：

```bash
python -B scripts/skeleton.py --input /input --out /app/output/result.json   # Step 0：先落盘骨架
python -B scripts/main.py --input /input --output /app/output --work /app/work  # Step 1：来源快照
# Agent 按 Step 2–3 精读材料、写 /app/output/draft.md、填 fact_source_map
python -B scripts/selftest.py --out /app/output/result.json                    # Step 4：自检
```

示意输出（`result.json` 节选，**示例，非正式案例结果**）：

```json
{
  "case_id": "demo-001",
  "status": "done",
  "artifact": {"path": "draft.md", "type": "markdown", "description": "立项依据章节稿"},
  "fact_source_map": [
    {"claim": "瓶颈已缓解至X%", "kind": "number", "source_ref": "paper.md",
     "locator": "p1 §2", "quote": "…", "status": "mapped"}
  ],
  "version_boundary": {"modification_diff": [], "manual_confirm": [],
                        "material_gaps": [], "anomalies": []},
  "sources": [{"path": "paper.md", "type": "markdown", "sha256": "<sha256>"}],
  "uncertainty": [],
  "change_log": [{"time": "…", "action": "…", "reason": "…", "impact": "…"}]
}
```

## 9. 严禁事项

- 不预写正式测试答案/白板；不编造 benchmark 分数、实验数据、DOI、卷期页码、图表内容或运行结果。
- 不把文件名/领域标签当事实；不把未完成的 `in_progress`/`partial` 改成 `done`。
- 不提交隐藏测试或白板；平台提供或生成任何所需对照。
- 引用的 `quote` 必须是材料中逐字存在的片段；缺失就写 `pending_human` 或 `not_reported`。
