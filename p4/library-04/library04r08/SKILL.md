---
name: research-kb-qa
description: 科研知识库资料管理与带来源引用的问答 Skill。盘点输入文件、目录与权限，提取元数据与版本关系，按内容哈希去重，建立可定位的证据回链，并对多文件问答逐条给出逐字引用与不确定性分级；交付可复核、可交接、可复跑的清单与操作记录。当需要整理科研资料、维护版本关系或做带引用问答并产出 result.json 时使用。
version: 1.0.0
---

# 科研知识库资料管理与带来源引用的问答

## 1. 角色与何时使用

本 Skill 把「盘点资料 → 组织元数据/版本 → 带来源引用的问答 → 可复核交付」固化为可复跑流程，解决「回答通顺但引用对不上文件/段落/版本」的问题。适用于跨目录、跨格式、含重复副本与历史版本的科研文件集合，需要产出文件清单、元数据、版本关系、哈希证据与带来源引用的问答结果。不适用于无材料的一次性聊天问答，或已有专用工具链且无需复核的场景。

核心判断标准只有三条：每个关键论断都能落到具体文件、具体版本、具体段落；库内确实没有依据的问题敢于说「没有」并说明缺什么；换一个人拿着提交记录能重走同一问答。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束，可能含 `output_contract`、`questions`、材料路径）与 `/input/task.md`（题目说明）。
- 材料（PDF、Markdown、文本、Office 等）位于 `/input` 下或其子目录。必须读实际文件，不依据文件名标签臆测内容。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`。
- 脚本只用 Python 标准库，用相对路径调用：`scripts/main.py`、`scripts/pipeline.py`、`scripts/selftest.py`。缺 `pdftotext` 时自动降级并记录，不编造文本层。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：取自 `case.json` 的 case_id/id/challenge_id/armiId。
- `output_contract` 的全部字段：**原样回填全部键**，不改名、不删除未知键；缺值给 `null`/`[]`/`{}`。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度。
- `sources`：文件与来源清单（相对路径、SHA256、类型、大小、来源、版本标记）。
- `uncertainty`：逐条不确定性分级与理由。
- `pending_human`：需人工介入或材料缺失的待办项。
- `change_log`：每步操作记录（步骤、时间、动作、状态）。
- 另附 `answers`（逐问答案+证据回链）、`library`（六维度清单摘要）、`limitations`、`errors`、`degradations`、`runtime`。
- 附带可读产物：`inventory.md`（清单）、`evidence_map.json`（证据映射）。

## 4. 执行流程与预算

总预算：**900 秒、64 次模型请求**。硬规则：**前 3 分钟内先跑 scaffold 落盘一版合法 result.json 骨架，之后只做增量改进**；任何一步失败不得让进程崩溃，也不得编造数据。

| 阶段 | 时间 | 请求 | 步骤 | 输入 → 输出 |
|---|---|---|---|---|
| 0 骨架 | 0–3 min | ~5 | 读 `/input/case.json`、`/input/task.md`；跑 `scaffold` | /input → `/app/output/result.json` + `/app/work/case_summary.json` |
| 1 盘点 | 3–12 min | ~20 | 跑 `inventory`；读清单；抽查关键文件 | /input → `/app/work/inventory.json` + `evidence_index.json` |
| 2 问答 | 12–14 min | ~15 | 逐问检索→摘录逐字证据→写 `answers.json` | 材料+索引 → `/app/work/answers.json` |
| 3 汇总 | 14–15 min | 剩余 | 跑 `finalize`；跑 `selftest`；按自检清单补漏 | answers+inventory → 最终 `/app/output/result.json` |

`answers.json` 结构：

```json
{
  "output": {"<output_contract 键>": "<该键的值>"},
  "answers": [
    {"question": "...", "answer": "...",
     "evidence": [{"file": "materials/paper.md", "sha256": "...", "locator": "materials/paper.md#L12-L15", "quote": "逐字片段", "confidence": "certain"}],
     "uncertainty": "certain", "missing": null}
  ],
  "uncertainty": [{"level": "conflict", "item": "...", "reason": "两来源不一致", "sources": ["a", "b"]}]
}
```

## 5. 评分维度对齐（本版侧重）

本 Skill 不做「通用大而全」流程，而是把题目五个评分维度**逐条映射到阶段动作与自检项**，缺一项即视为未完成：

- **实际效果 40 分** → 阶段 1 盘点 + 阶段 2 问答 + 自检项 1–4：六维度完整性、逐字证据、回链定位、版本/权限处置。
- **使用增益 25 分** → 阶段 1 一次 `inventory` 建立确定性索引（减少重复检索与人工核对），自检项 5–6：增益可验证性靠 `operation_evidence` 支撑。
- **创新增益 15 分** → 内容哈希去重、版本基名分组、缺证判据与冲突聚合（自检项 7–8）。
- **可靠体验 10 分** → CLI 与状态码、降级矩阵、异常处理（自检项 9–11）。
- **生态贡献 10 分** → SKILL.md 与 references 文档完整、脚本标准库可复用、清单可交接（自检项 12–14）。

详细映射表见 `references/scoring-alignment.md`。

## 6. 自检清单（逐条勾选）

- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] **正确性**：每条答案引用真实、定位准确、`quote` 逐字对应，版本与权限处置正确。
- [ ] **可核验性**：`operation_evidence` 含哈希、版本记录、引用定位，可让他人复跑核对。
- [ ] **不确定性**：证据不足/冲突/不可读/权限受限均分级提示并说明缺什么。
- [ ] **可复现**：同一输入两次运行结果一致（确定性排序、固定哈希、单点时间戳）。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `errors`/`degradations`，未完成不写完成。
- [ ] **契约回填**：result.json 含 case_id 与 output_contract 全部字段，JSON 合法。
- [ ] **缺证判据**：对库内无依据的问题返回 `insufficient_evidence`，写清缺什么，不补造答案。
- [ ] **冲突聚合**：同一指标两来源不一致时全部记录并标 `source_conflict`，不隐藏。
- [ ] **运行稳定**：空目录、坏 JSON、坏编码、缺 `pdftotext`、权限不足均不崩溃且产出合法降级结果。
- [ ] **CLI 与状态码**：`scaffold`/`inventory`/`finalize` 三个子命令可执行，异常退出码非 0 且信息可读。
- [ ] **文档完整**：SKILL.md + references 说明用途、契约、方法、边界与自查。
- [ ] **资源复用**：脚本只用标准库、相对路径，他人可直接运行。
- [ ] **交接价值**：`inventory.md` + `evidence_map.json` 可作交接清单与证据索引。

## 7. 降级策略

- 空输入目录：`status=insufficient_evidence`，退出码 0，写合法结果并列出缺口。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料。
- 缺 `pdftotext`/OCR：PDF 无文本层标 `unreadable`/`needs_ocr`，不编造文本。
- 写盘失败：降级为最小合法产物 `{"case_id","status","sources","uncertainty","change_log"}`，不让进程崩溃。
- 超时/超请求：先落盘部分结果，`status=partial` 并记录未完成项。
- 材料缺失/无法判断/运行失败：分别写入 `pending_human`、`limitations`、`errors`，如实说明，不把未完成写成完成。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
# Agent 按第 4 阶段写 /app/work/answers.json
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work --answers /app/work/answers.json
python3 scripts/selftest.py
```

证据定位与六维度细则见 `references/checklist.md`；评分维度映射见 `references/scoring-alignment.md`。
