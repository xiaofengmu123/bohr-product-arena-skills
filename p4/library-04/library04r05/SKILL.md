---
name: library-contract-qa
description: 科研知识库资料管理与带来源引用的问答 Skill。用于盘点输入文件/目录/权限、提取元数据与版本关系、按内容哈希去重、建立可定位的证据回链，并对多文件问答逐条给出逐字引用与不确定性分级；result.json 逐键对齐题目 output_contract 并附 change_log，可复核、可交接、可复跑。当需要整理科研资料、维护版本关系或做带引用问答并交付结构化结果时使用。
version: 2.0.0
---

# 科研知识库资料管理与带来源引用的问答

## 1. 角色与何时使用

本 Skill 把“盘点资料 → 组织元数据/版本 → 带来源引用问答 → 可复核交付”固化为可复跑流程，解决“回答通顺但引用对不上文件/段落/版本”的问题。适用于跨目录、跨格式、含重复副本与历史版本的科研文件集合，需要产出文件清单、六维度元数据、版本关系、哈希证据与逐字引用的问答结果。不适用于无材料的一次性聊天；不把示例当正式案例结果。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束，可能含 `output_contract`、`questions`、材料路径）与 `/input/task.md`（题目说明）。
- 材料（PDF、Markdown、文本、Office 等）位于 `/input` 下或由 `case.json` 指明的子目录。必须读实际文件，不依据文件名标签臆测内容。
- 工作目录 `/app/work`，最终产物写 `/app/output/`（含 `result.json`）。
- 脚本仅用 Python 标准库，相对路径调用：`scripts/main.py`、`scripts/inventory.py`、`scripts/selftest.py`；缺 `pdftotext` 时自动降级并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：取自 `case.json` 的 `case_id / id / challenge_id`（首个非空）。
- `output_contract` 的全部字段：**逐键原样回填**，不改名、不删除未知键；嵌套 dict 保留结构（叶子 `null`）、list 给 `[]`、标量给 `null`；Agent 在 `work/answers.json → output` 给出的值覆盖对应叶子。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度。
- 框架字段：`answers`、`library`（六维度摘要）、`sources`（相对路径、SHA256、类型、大小、版本 token）、`uncertainty`、`pending_human`、`change_log`、`evidence_map`、`contract_fidelity`、`errors`、`degradations`、`failures_recovery`、`runtime`。
- `contract_fidelity`：对每个 output_contract 键记录是否回填、来源（agent/echoed），复核标准 `preserved_keys == total_keys`。
- 附带可读产物：`/app/work/inventory.json`（含 `text_index` 证据索引）、`/app/work/case_summary.json`。

## 4. 执行流程与预算

总预算 **900 秒、64 次模型请求、每响应含推理 16384 token**。硬规则：**前 3 分钟内先落盘一版合法 partial 骨架，之后只做增量改进**；任何一步失败不得崩溃，也不得编造数据或评分。

| 阶段 | 时间 | 步骤 | 命令 | 输入 → 输出 |
|---|---|---|---|---|
| 0 骨架 | 0–3 min | 读 case.json/task.md，写骨架 | `python3 scripts/main.py --action scaffold` | /input → `/app/output/result.json`(partial) + `work/case_summary.json` |
| 1 盘点 | 3–12 min | 六维度清单 + 哈希 + 版本 + 证据索引 | `python3 scripts/main.py --action inventory` | /input → `work/inventory.json` |
| 2 问答 | 12–14 min | 逐问检索→摘逐字证据→写 answers.json | （Agent 手写） | 材料+索引 → `work/answers.json` |
| 3 汇总 | 14–15 min | 契约回填、证据核对、自检 | `python3 scripts/main.py --action finalize` | answers+inventory → 最终 `result.json` |

`answers.json` 结构（详见 `references/contract.md`）：

```json
{"output": {"<output_contract 键>": "<值>"},
 "answers": [{"question_id": "q1", "question": "...", "answer": "...",
   "evidence": [{"file": "materials/paper.md", "sha256": "...", "locator": "materials/paper.md#L12-L15", "quote": "逐字片段", "confidence": "certain"}],
   "uncertainty": "certain", "missing": null}],
 "uncertainty": [{"level": "conflict", "item": "...", "reason": "两来源不一致", "sources": ["a","b"]}],
 "pending_human": []}
```

## 5. 证据回链与不确定性（硬规则）

- 每个关键论断必须带 `file`（相对路径）、`sha256`、`locator`（`materials/paper.md#L12-L15`；PDF 用 `#p3`）、`quote`（原文逐字片段）。
- `finalize` 回读原文件做归一化子串核对，写 `verified: true/false/null`；`false`（定位/引用有误）或 `null`（无 pdftotext/文件缺失）使 `status` 回到 `partial` 并写入 `degradations`。
- 不得虚构引用、DOI、数值、页码；拿不到就写 `not_reported` 或 `pending_human`。
- 不确定性分级：`certain`（逐字+单一来源+verified）、`likely`（推断/摘要）、`conflict`（来源矛盾，全部记录并标 `source_conflict`）、`insufficient_evidence`（库内无依据，写明缺什么）、`unreadable`（不可读/扫描件无文本层/权限不足）。冲突不隐藏。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] **契约回填**：result.json 含 case_id 与 output_contract 全部字段，`contract_fidelity.preserved_keys == total_keys`，JSON 合法。
- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项（`summary.six_dimensions`）。
- [ ] **正确性**：每条答案引用真实、定位准确、`quote` 逐字命中且 `verified=true`，版本与权限处置正确。
- [ ] **可核验性**：`sources` 含哈希与版本记录、`evidence` 含 locator+quote+verified，他人可复跑核对。
- [ ] **不确定性**：证据不足/冲突/不可读/权限受限均分级提示并说明缺什么。
- [ ] **可复现**：同一输入两次运行结果一致（walk/列表确定性排序、固定 SHA256、稳定 JSON 顺序）。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `failures_recovery`，未完成不写完成。
- [ ] **自检通过**：`python3 scripts/selftest.py` 退出码 0，空输入/坏 JSON/缺 case.json 三种降级均产合法结果。

## 7. 降级策略

- 空输入目录：`status=insufficient_evidence`，退出码 0，写合法结果并列出缺口。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料；`case.json` 缺失或不可解析时 `status=input_error`。
- 缺 `pdftotext`/OCR：PDF 无文本层标 `unreadable`/`needs_ocr`，证据 `verified=null`，不编造文本。
- Office/扫描件无文本层：标 `unreadable`，在 `pending_human` 建议人工/OCR，不冒充可读。
- 写盘失败：降级为最小合法产物 `{"case_id","status":"partial","sources","uncertainty","change_log"}`，不让进程崩溃。
- 超时/超请求：先落盘部分结果，`status=partial` 并记录未完成项。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 scripts/main.py --action scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py --action inventory --input /input --output /app/output --work /app/work
# Agent 按第 4 阶段写 /app/work/answers.json
python3 scripts/main.py --action finalize --input /input --output /app/output --work /app/work
python3 scripts/main.py --action selftest --input /input --output /app/output --work /app/work
```

六维度规则与证据定位细则见 `references/checklist.md`；result.json 契约与 change_log 细则见 `references/contract.md`。