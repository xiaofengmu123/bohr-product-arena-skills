---
name: research-kb-qa
description: 科研知识库资料管理与带来源引用的问答 Skill。采用"契约优先 + 单一清单 + 追加式操作账本"架构：先校验并回填输出契约，再盘点输入文件/目录与权限、提取元数据与版本关系、按 SHA256 去重、建立可定位证据回链，并对多文件问答逐条给出逐字引用与不确定性分级；产出可复核、可交接、可复跑的清单、证据映射与 result.json。当需要整理科研资料、维护版本关系或做带引用问答并交付结构化结果时使用。
version: 1.1.0
---

# 科研知识库资料管理与带来源引用的问答

## 1. 角色与何时使用

本 Skill 把“盘点资料 → 组织元数据/版本 → 带来源引用的问答 → 可复核交付”固化为可复跑流程，解决“回答通顺但引用对不上文件/段落/版本”的问题。适用于跨目录、跨格式、含重复副本与历史版本的科研文件集合，需产出文件清单、元数据、版本关系、哈希证据与带来源引用的问答结果。不适用于无材料的一次性聊天问答，或已有专用工具链且无需复核的场景。

与其他做法不同，本包**契约优先**：先把 `output_contract` 校验并原样回填，再进入问答，避免“做完才发现契约字段缺失”。全程维护**单一中间表示** `/app/work/state.json`（清单、来源、答案、账本同源），并用**追加式操作账本**记录每一步，保证可回放、可续跑、可复核。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束，可能含 `output_contract`、`questions`、材料路径）与 `/input/task.md`（题目说明）。
- 材料（PDF、Markdown、文本、Office、ZIP 等）位于 `/input` 下，或由 `case.json` 指明的子目录。必须读实际文件，不依据文件名标签臆测内容。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`。
- 脚本以相对路径 `scripts/engine.py` 调用，只依赖 Python 标准库；缺 `pdftotext` 时自动降级并记录到 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`，合法 JSON，至少含：

- `case_id`：取自 `case.json` 的 `case_id`/`caseId`/`id`/`challenge_id`（按此优先级）。
- `output_contract` 的全部字段：**原样回填全部键**，不改名、不删除未知键；缺值给 `null`/`[]`/`{}`。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度。
- `sources`：文件来源清单（相对路径、SHA256、类型、大小、版本标记、来源）。
- `uncertainty`：逐条不确定性分级与理由。
- `pending_human`：需人工裁决的项（冲突、缺证、不可读、权限受限、缺软件）。
- `change_log`：追加式操作记录（seq、时间、阶段、动作、状态）。
- 另附 `answers`（逐问答案+证据回链）、`library`（六维度摘要）、`errors`、`degradations`、`limitations`、`runtime`、`generated_artifacts`。
- 附带可读产物：`inventory.md`（清单）、`evidence_map.json`（证据映射）。

## 4. 执行流程与预算

总预算 **900 秒、64 次模型请求**，单次回复 ≤16384 token。硬规则：**前 3 分钟内必须先落盘一版合法 result.json 骨架（`status=partial`），此后只做增量改进**；任何一步失败不得让进程崩溃，也不得编造数据。

| 阶段 | 关键动作 | 脚本/角色 | 输入 → 输出 |
|---|---|---|---|
| 0 骨架 | 读 case.json/task.md → 回填契约 → 立即落盘 partial result.json + state.json | `main.py scaffold` | /input → /app/output/result.json + /app/work/state.json |
| 1 盘点 | 六维度清单、SHA256、重复/版本分组、权限 | `main.py inventory` | /input → state.json（补 library/sources） |
| 2 问答 | 逐问检索→摘录逐字证据→写 answers | Agent 写 /app/work/answers.json | 材料+索引 → answers.json |
| 3 汇总 | 合并、契约复核、出 inventory.md / evidence_map.json | `main.py finalize` | answers+state → 最终 result.json |

`answers.json` 结构（Agent 在阶段 2 填写，`finalize` 合并）：

```json
{
  "answers": [
    {"question": "...", "answer": "...",
     "evidence": [{"file": "materials/paper.md", "sha256": "...",
       "locator": "materials/paper.md#L12-L15", "quote": "逐字片段",
       "confidence": "certain"}],
     "uncertainty": "certain", "missing": null}
  ],
  "uncertainty": [{"level": "conflict", "item": "...", "reason": "两来源不一致", "sources": ["a","b"]}]
}
```

## 5. 证据回链与不确定性（硬规则）

- 每个关键论断必须带 `file`（相对路径）、`sha256`、`locator`（如 `materials/paper.md#L12-L15`、PDF 用 `#p3`）、`quote`（原文逐字片段）。
- 不得虚构引用、DOI、数值、页码；拿不到就写 `not_reported` 或列入 `pending_human`。
- 不确定性分级：`certain`（逐字+单一来源）、`likely`（推断/摘要）、`conflict`（来源矛盾，全部记录并标 `source_conflict`）、`insufficient_evidence`（库内无依据）、`unreadable`（不可读/扫描件无文本层/权限不足）。
- 冲突不隐藏：同一指标两来源不一致时两个都记录。

## 6. 自检清单（提交前逐条勾选；对应评分维度）

- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] **正确性**：每条答案引用真实、`locator` 定位准确、`quote` 逐字对应；版本与权限处置正确。
- [ ] **可核验性**：含 SHA256、版本记录、引用定位、追加式账本，可让他人复跑核对。
- [ ] **不确定性**：证据不足/冲突/不可读/权限受限均分级提示并说明缺什么。
- [ ] **可复现**：同一输入两次运行结果字节级一致（确定性排序、固定哈希、固定时间基）。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `failures_recovery`，未完成不写完成。
- [ ] **契约回填**：result.json 含 `case_id` 与 output_contract 全部字段，JSON 合法（`selftest.py` 校验）。
- [ ] **降级诚实**：缺数据/缺软件在 `limitations`/`degradations` 如实标注，不编造。

## 7. 降级策略（硬规则）

- 空输入目录：`status=insufficient_evidence`，退出码 0，写合法结果并列出缺口。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料；case.json 坏则 `status=input_error`。
- 缺 `pdftotext`/OCR：PDF 无文本层标 `unreadable`/`needs_ocr`，不编造文本。
- 写盘失败：降级为最小合法产物 `{"case_id","status":"partial","sources","uncertainty","change_log","limitations"}`，不让进程崩溃。
- 超时/超请求：先落盘部分结果，`status=partial` 并记录未完成项与 `limitations`。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
python3 scripts/main.py answer    --input /input --output /app/output --work /app/work
# Agent 按第 4/5 节填写 /app/work/answers.json（或直接改 state.json 的 answers）
python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work
python3 scripts/main.py run       --input /input --output /app/output --work /app/work  # 一键全流程（无问答时 status=partial）
python3 scripts/selftest.py       # 用合成最小输入跑自检
```

六维度判定、证据定位与复核细则见 `references/checklist.md`；退化场景矩阵见 `references/degradation.md`。