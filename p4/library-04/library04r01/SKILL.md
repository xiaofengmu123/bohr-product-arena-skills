---
name: research-kb-qa-evidence
description: 科研知识库资料管理与带来源引用的问答 Skill。用于盘点输入文件/目录与权限、提取元数据与版本关系、按内容哈希去重、建立可定位且可回核的证据回链，并对多文件问答逐条给出逐字引用与不确定性分级；核心增强是每条结论都带 locator+逐字 quote+quote_sha256 的细粒度双向 evidence_map。当需要整理科研资料、维护版本关系或做带引用问答并产出 result.json 时使用。
version: 2.0.0
---

# 科研知识库资料管理与带来源引用的问答（证据溯源强化版）

## 1. 角色与何时使用

本 Skill 把“盘点资料 → 组织元数据/版本 → 带来源引用的问答 → 可复核交付”固化为可复跑流程，
解决“回答通顺但引用对不上文件/段落/版本”的问题。适用于跨目录、跨格式、含重复副本与历史版本的
科研文件集合，需要产出文件清单、元数据、版本关系、哈希证据与带来源引用的问答结果。
**本版侧重点为证据溯源强化**：每条结论必须带 `locator` 与逐字 `quote`，并由脚本自动回核，
`evidence_map` 采用细粒度双向结构（claim↔evidence、`quote_sha256`、`char_span`、`source_role`）。
不适用：无材料的一次性聊天问答；已有专用工具链且无需复核的场景。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束，可能含 `output_contract`、`questions`、材料路径）
  与 `/input/task.md`（题目说明）；以实际文件为准，不依据文件名标签臆测内容。
- 材料（PDF、Markdown、文本、Office 等）位于 `/input` 下，或由 `case.json` 指明的子目录。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`。
- 脚本用相对路径调用，仅依赖 Python 标准库；缺 `pdftotext` 时自动降级并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，含：

- `case_id`：取自 `case.json` 的 case_id/id/challenge_id。
- `output_contract` 的全部字段：**原样回填全部键**，不改名、不删除未知键；缺值给 `null`/`[]`/`{}`，
  值由 Agent 写入 `answers.json` 的 `output` 后由 `finalize` 合并回填。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度。
- `sources`（文件与来源清单：相对路径、SHA256、类型、大小、版本标记）、
  `uncertainty`（逐条分级与理由）、`change_log`（每步操作记录：步骤/时间/动作/状态）。
- 另附 `answers`（逐问答案+证据回链）、`evidence_map`（细粒度双向证据映射）、
  `library`（六维度清单摘要）、`errors`、`degradations`、`runtime`。
- 附属可读产物：`inventory.md`（清单）、`evidence_map.json`（证据映射）。

## 4. 执行流程与预算

总预算：**900 秒、64 次模型请求**。硬规则：**前 3 分钟内先跑 `scaffold` 落盘一版合法
result.json 骨架（status=partial，回填 output_contract），之后只做增量改进**；任何一步失败
不得让进程崩溃，也不得编造数据。

| 阶段 | 时间 | 请求 | 步骤 | 输入 → 输出 |
|---|---|---|---|---|
| 0 骨架 | 0–3 min | ~5 | 读 `/input/case.json`、`/input/task.md`；跑 `scaffold.py` | /input → `/app/output/result.json` + `/app/work/case_summary.json` |
| 1 盘点 | 3–12 min | ~20 | 跑 `inventory.py`；读清单；抽查关键文件 | /input → `/app/work/inventory.json` + `evidence_index.json` |
| 2 问答 | 12–14 min | ~15 | 逐问检索→摘录逐字证据→写 `answers.json` | 材料+索引 → `/app/work/answers.json` |
| 3 汇总 | 14–15 min | 剩余 | 跑 `finalize.py`；按自检清单补漏 | answers+inventory → 最终 `/app/output/result.json` |

`answers.json` 结构（Agent 在阶段 2 写入）：

```json
{
  "status": "ok",
  "output": {"<output_contract 键>": "<该键的值>"},
  "answers": [
    {"question": "...", "answer": "...",
     "evidence": [{"file": "materials/paper.md", "sha256": "...",
                   "locator": "materials/paper.md#L12-L15",
                   "quote": "逐字片段", "confidence": "certain",
                   "source_role": "primary"}],
     "uncertainty": "certain", "missing": null}
  ],
  "uncertainty": [{"level": "conflict", "item": "...", "reason": "两来源不一致",
                   "sources": ["a", "b"]}]
}
```

`finalize` 会把该结构归一为 `evidence_map_v2`，自动补填 `quote_sha256`、`char_span`、
`used_by` 并逐字回核 `verification.status`。

## 5. 证据回链与不确定性（硬规则）

- 每个关键论断必须带 `file`（相对路径）、`sha256`、`locator`、`quote`（原文逐字片段）。
- 不得虚构引用、DOI、数值、页码；拿不到就写 `not_reported` 或 `pending_human`。
- 不确定性分级：`certain`（逐字+单一来源）、`likely`（推断/摘要）、`conflict`（来源矛盾，
  全部记录并标 `source_conflict`）、`insufficient_evidence`（库内无依据）、
  `unreadable`（不可读/扫描件无文本层/权限不足）。冲突不隐藏，两来源都记录。
- 详细 locator 规范与 evidence_map_v2 结构见 `references/evidence_spec.md`。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] **正确性**：每条答案引用真实、定位准确、`quote` 逐字对应，版本与权限处置正确。
- [ ] **可核验性**：`evidence_map` 含 file+sha256+locator+quote+quote_sha256，且回核后
  `verification.status=verified`；`runtime.deterministic_fingerprint` 存在。
- [ ] **不确定性**：证据不足/冲突/不可读/权限受限均分级提示并说明缺什么。
- [ ] **可复现**：同一输入两次运行结果一致（确定性排序、固定哈希、固定 quote 规范化）。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `errors`/`degradations`/`change_log`，
  未完成不写完成。
- [ ] **契约回填**：result.json 含 case_id 与 output_contract 全部字段，JSON 合法。
- [ ] **自检脚本**：交付前运行 `python3 scripts/selftest.py`，退出码必须为 0。

## 7. 降级策略

- 空输入目录：`status=insufficient_evidence`（缺 case.json 为 `input_error`），退出码 0，
  写合法结果并列出缺口。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料。
- 缺 `pdftotext`/OCR：PDF 无文本层标 `unreadable`/`needs_ocr`，不编造文本。
- 单文件 > 2MB：跳过并记录 `size_skipped`，其余继续。
- 写盘失败：降级为最小合法产物 `{"case_id","status":"partial","sources","uncertainty","change_log"}`，
  不让进程崩溃。
- 超时/超请求：先落盘部分结果，`status=partial` 并记录未完成项。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
# Agent 按第 4 阶段写 /app/work/answers.json
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work \
  --answers /app/work/answers.json
python3 scripts/selftest.py   # 交付前自检
```

详细六维度规则与证据定位规范见 `references/checklist.md` 与 `references/evidence_spec.md`。
