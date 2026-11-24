---
name: thesis-longdoc-writing
description: 学位论文与长文档写作 Skill。读取科研材料与任务约束，完成章节规划、生成/改写/校对、跨章节一致性检查、引用绑定与改稿记录，交付可编辑、可复核的写作产物与 result.json。当需要围绕给定科研材料完成学位论文或长文档的写作、修改、校对、引用、双语或结构化任务时使用。
version: 1.0.0
---

# 学位论文与长文档写作 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 在长文档写作任务中稳定做到四件事：**立得住**（章节与提纲、论证链条、字数分配对应）、**连得上**（术语、符号、定义、编号、图表引用、结论跨章一致）、**改得动**（局部修改只动需要动的地方，改动可解释可回退）、**查得清**（每个论断、每条引用、每次改动可回溯到原文并附逐字证据）。

适用：给定科研材料（论文、数据、提纲、底稿、导师/评审意见等）后，产出章节、摘要、改写、校对、引用表、术语表、改稿说明等写作产物，并保留来源与修改记录。不适用：无材料的一次性闲聊、与写作无关的检索、没有真实材料却要求产出正式结果的场景。

## 2. 输入契约（运行时）

运行时输入固定为 `/input/case.json` 与 `/input/task.md`；材料（PDF、md、docx、数据表等）按题目放在 `/input` 下。PDF 以原文为准，不存在预先抽取的答案层，必须读实际文件而非按标签猜内容。

- **先发现、再假设**：先运行 `python -B scripts/main.py bootstrap --input /input --output /app/output --work /app/work` 扫描 `/input`，识别 `case.json`、`task.md`、`materials/` 及其他材料文件；不假设固定文件名或键名。
- 材料文本以 `utf-8-sig` 读取并容错（BOM、坏编码降级）；单文件超 2MB 跳过并记入 `degradations`；坏 JSON 记入 `errors` 后继续处理其余材料，进程不崩溃。
- `case.json` 中的 `output_contract`（顶层或常见嵌套位置）必须**原样回填**到 `result.json` 顶层：缺值填 `null`/`[]`，不改名、不删未知键。
- 工作目录 `/app/work`（中间产物、registry、manifest），最终产物与 `result.json` 写到 `/app/output/`。

## 3. 输出契约（/app/output/result.json）

`result.json` 必须包含：`case_id`、题目 `output_contract` 的**全部字段**（原样回填，缺值 `null`/`[]`），并额外带以下标准字段：

- `status`：`ok | partial | insufficient_evidence | input_error`，如实标注，不编造结论。
- `sources`：`[{path, kind, locator, note}]`，事实材料的定位。
- `uncertainty`：`[{item, reason, level}]`，不确定项及原因。
- `pending_human`：需人工确认项（冲突、缺口、存疑引用）。
- `limitations`：材料/工具/篇幅限制的如实声明。
- `errors`、`degradations`：异常与降级记录。
- `change_log`：`[{time, content, reason}]`，每次写回都追加一条。
- `runtime`：`{started_at, seconds, model_requests}`。

主产物必须是**可打开、可编辑、可复核**的文件：文本类给 `.md`/`.json`/`.csv`（`manuscript.md`、`term_table.md`、`evidence_map.json`、`change_record.md` 等）；图表类给可编辑 SVG + 真实 PDF；办公文档给 `.docx`/`.pptx`/`.xlsx`，并另存真实 PDF/PNG 预览。所有事实、数字、引用、图元必须能指回材料中的逐字片段（locator + quote），冲突信息不隐藏（标 `source_conflict`）。

## 4. 执行流程（总预算 900 秒、64 次模型请求、每次响应 ≤16384 token）

| 步骤 | 时间预算 | 模型请求 | 输入 → 输出 |
|---|---|---|---|
| Step 0 落盘骨架 | 0–2 分钟 | ≤3 | `/input` → 运行 `bootstrap`，写合法 `result.json`（case_id + output_contract 全字段 null/[]，status=partial，change_log 首条） |
| Step 1 读材料建事实底稿 | 2–6 分钟 | ≤8 | 读 `task.md`、全部材料 → 抽取事实、数字、术语、图表编号，建 `work/input_manifest.json` |
| Step 2 主产物写作 | 6–12 分钟 | ≤28 | 事实底稿 + 提纲 → 写 `manuscript.md`（或题目指定体裁），每段绑定来源 |
| Step 3 一致性/引用核验 | 12–14 分钟 | ≤17 | 运行 `scripts/consistency.py scan` 与 `scripts/evidence.py verify` → 写 `term_table.md`、`evidence_map.json`、`change_record.md` |
| Step 4 收尾自检 | 14–15 分钟 | ≤8 | 运行 `finalize` 校验补齐 → 补 limitations → 跑自检清单 |

**硬性要求：前 3 分钟内必须落盘一版合法 result.json 骨架，之后只做增量改进**（每次写回追加 `change_log`）。任何一步异常都不覆盖已有合法产物；先写 partial，再升级为 ok。

脚本调用一律用相对路径：`python -B scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。

## 5. 自检清单（对应评分维度，交付前逐条勾选）

- [ ] 实际效果：`manuscript.md` 按提纲写出可用的章节，而非通用建议；结论、数据、术语、符号与材料一致（**结果正确性**）。
- [ ] 实际效果：提纲要求的章节、研究问题/方法/结果/讨论/局限、图表、参考文献、附录齐全（**完整性**）。
- [ ] 实际效果：每段文字可回溯到大纲条目、材料段落、具体文献（`evidence_map.json` 有 locator + quote）（**可核验性**）。
- [ ] 使用增益：`term_table.md` 统一术语/缩写，`consistency.py` 检出重复编号、缺号、同缩写异定义，减少人工通读（**质量/效率增益**）。
- [ ] 创新增益：`scripts/evidence.py verify` 对每条引用做逐字/空白归一化核验，标记未命中，避免虚构引用（**新增能力**）。
- [ ] 可靠体验：空输入→`insufficient_evidence` 退出码 0；坏 JSON→`input_error` 记 `errors`；超长截断→`degradations`；重复运行结果一致（**稳定性**）。
- [ ] 生态贡献：SKILL.md + scripts + references 齐全，他人/他 Agent 可按说明独立复现（**复用性**）。

## 6. 降级策略（缺数据/缺软件如实标注，严禁编造）

- 材料不足：`status=insufficient_evidence`，在 `limitations` 列出缺口，在 `pending_human` 列出待补项；**不**用示例内容或记忆填充。
- 引用找不到原文/结论被改写：`evidence.py verify` 未命中的引用标 `unverified`，写入 `uncertainty` 或 `pending_human`，不改写原文献结论。
- 编号不连续/章节间结论冲突：`consistency.py` 检出后标 `source_conflict`，两个说法都记录，不擅自取舍。
- 文档格式不支持/超长：记 `degradations`，能转文本的转文本，不能的标 `not_supported`。
- 缺软件（如无 LaTeX/Office 渲染器）：产物降级为 `.md`/`.json` 并在 `limitations` 说明缺失的预览，绝不伪造 PDF/PNG。
- 任何失败都写合法 partial 结果并保持进程退出码 0（对可恢复错误），让平台能拿到骨架而不是空目录。

## 7. 脚本与资源

| 文件 | 用途 |
|---|---|
| `scripts/main.py` | `bootstrap`（骨架）、`finalize`（校验补齐+content_hash）、`selftest`（离线自检） |
| `scripts/consistency.py` | `scan` 抽取章节/图表/公式编号与术语候选，`check` 检重复编号、缺号、同缩写异定义 |
| `scripts/evidence.py` | `verify`/`verify-batch` 核验引用逐字/空白归一化命中并给 locator，`make` 生成规范证据记录 |
| `references/consistency-checklist.md` | 跨章节一致性检查清单 |
| `references/evidence-rules.md` | 证据溯源与引用核验规则 |

提交前本地自检：`python -B scripts/main.py selftest`（应显示全部 PASS 且退出码 0）。
