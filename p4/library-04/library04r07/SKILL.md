---
name: research-kb-qa
description: 科研知识库资料管理与带来源引用的问答 Skill。用于盘点输入文件/目录与权限、提取元数据与版本关系、按内容哈希去重、建立可定位的证据回链，并对多文件问答逐条给出逐字引用与不确定性分级；交付可复核、可交接、可复跑的清单与操作记录。当需要整理科研资料、维护版本关系或做带引用问答并产出 result.json 时使用。
version: 1.1.0
---

# 科研知识库资料管理与带来源引用的问答

## 1. 角色与何时使用

本 Skill 把「盘点资料 → 组织元数据/版本 → 带来源引用的问答 → 可复核交付」固化为可复跑流程，解决「回答通顺但引用对不上文件/段落/版本」的问题。适用于：跨目录、跨格式、含重复副本与历史版本的科研文件集合，需要产出文件清单、元数据、版本关系、哈希证据与带来源引用的问答结果。不适用于：无材料的一次性聊天问答；已有专用工具链且无需复核的场景。

核心原则：**工具只做确定性的、可复算的机械工作（哈希、类型嗅探、版本/重复分组、逐字校验、落盘），语义判断（读材料、逐问检索、摘录逐字证据、分级不确定性）由 Agent 完成**。任何证据都必须能指回具体文件、具体版本、具体段落或页码；材料里没有依据的问题，必须敢说「没有」并说明缺什么。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束、可能含 `output_contract`、`materials` 路径）与 `/input/task.md`（题目说明）。运行时原始输入在这两个文件里，须实际读取，不依据文件名标签臆测内容。
- 材料（PDF、Markdown、文本、Office 等）位于 `/input` 下或由 `case.json` 指明的子目录。
- 工作目录 `/app/work`，最终产物与可编辑产物写入 `/app/output/`。
- 脚本用相对路径调用，仅依赖 Python 标准库，入口为 `scripts/main.py`，实现在 `scripts/core_impl.py`。缺 `pdftotext` 时自动降级并把对应 PDF 标为 `unreadable`/`needs_ocr`。

## 3. 输出契约（必须产出 `/app/output/result.json`）

- `case_id`：取自 `case.json` 的 `case_id`/`id`/`challenge_id`（按此优先级）。
- `output_contract`：题目 `case.json` 中 `output_contract` 的全部字段**原样回填全部键**，不改名、不删除未知键；缺值给 `null`/`[]`/`{}`（保持原类型提示）。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度，不把未完成写成完成。
- `sources`：文件与来源清单（相对路径、SHA256、类型、大小、mtime）。
- `uncertainty`：逐条不确定性分级与理由；`pending_human`：证据不足/不可读/冲突需人工复核的条目。
- `change_log`：每步操作记录（步骤、时间、动作、状态、详情）。
- 另附 `answers`（逐问答案 + 证据回链）、`library`（六维度清单摘要）、`evidence_map`（证据映射）、`errors`、`degradations`、`limitations`、`runtime`。
- 附带可读产物：`inventory.md`（清单）、`evidence_map.json`（证据映射）。

## 4. 执行流程与预算

总预算：**900 秒、64 次模型请求**。硬规则：**前 3 分钟内先跑 scaffold 落盘一版合法 partial result.json 骨架，之后只做增量改进**；任何一步失败不得让进程崩溃，也不得编造数据。

| 阶段 | 时间 | 请求 | 步骤 | 输入 → 输出 |
|---|---|---|---|---|
| 0 骨架 | 0–3 min | ~5 | 读 `/input/case.json`、`/input/task.md`；跑 `scripts/main.py scaffold` | /input → `/app/output/result.json` + `/app/work/case_summary.json` |
| 1 盘点 | 3–12 min | ~20 | 跑 `scripts/main.py inventory`；读清单；抽查关键文件 | /input → `/app/work/inventory.json` + `/app/output/inventory.md` |
| 2 问答 | 12–14 min | ~15 | 逐问检索→摘录逐字证据→写 `/app/work/answers.json` | 材料 + 索引 → `/app/work/answers.json` |
| 3 汇总 | 14–15 min | 剩余 | 跑 `scripts/main.py finalize`；按自检清单补漏 | answers + inventory → 最终 `/app/output/result.json` |

`answers.json` 结构（Agent 写）：

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

## 5. 证据回链与不确定性（硬规则）

- 每个关键论断必须带 `file`（相对路径）、`sha256`、`locator`（如 `materials/paper.md#L12-L15`、PDF 用 `#p3`）、`quote`（原文逐字片段）。
- 不得虚构引用、DOI、数值、页码；拿不到就写 `not_reported` 或 `pending_human`。
- 不确定性分级：`certain`（逐字 + 单一来源）、`likely`（推断/摘要）、`conflict`（来源矛盾，全部记录并标 `source_conflict`）、`insufficient_evidence`（库内无依据）、`unreadable`（不可读/扫描件无文本层/权限不足）。
- 冲突不隐藏：同一指标两来源不一致时两个都记录。
- `finalize` 阶段脚本会对每条证据自动校验：文件是否存在、SHA256 是否匹配、locator 行号/页码是否命中、quote 是否逐字出现，校验结果写入 `evidence[]._verified`，不通过的标 `invalid`/`unverified`，绝不静默放过。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] **正确性**：每条答案引用真实、定位准确、`quote` 逐字对应，版本与权限处置正确。
- [ ] **可核验性**：`operation_evidence`（`sources` + `evidence_map` + `change_log`）含哈希、版本记录、引用定位，可让他人复跑核对。
- [ ] **不确定性**：证据不足/冲突/不可读/权限受限均分级提示并说明缺什么（`uncertainty` + `pending_human`）。
- [ ] **可复现**：同一输入两次运行结果一致（确定性排序、固定哈希；时间字段为运行记录，不影响分析字段）。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `errors`/`degradations`/`failures_recovery`，未完成不写完成。
- [ ] **契约回填**：result.json 含 case_id 与 output_contract 全部字段，JSON 合法。
- [ ] **鲁棒性**：空输入、坏 JSON、坏编码、缺参数、超大文件、缺 pdftotext 均优雅降级（详见第 7 节），`scripts/selftest.py` 全部 PASS。

## 7. 降级策略（本版重点：鲁棒性，不编造）

- 空输入目录：`status=insufficient_evidence`，退出码 0，写合法结果并列出缺口。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，尽力恢复部分 JSON，继续处理其余材料，不崩溃。
- 缺参数/未知参数：`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`，自动创建目录。
- 超大文件（>2MB）：仍计算哈希、记录元数据，但跳过全文解析/逐字校验并写 `degradations`，避免内存爆炸。
- 缺 `pdftotext`/OCR：PDF 无文本层标 `unreadable`/`needs_ocr`，不编造文本。
- 写盘失败：降级为最小合法产物 `{"case_id","status":"partial","sources","uncertainty","change_log"}`，不让进程崩溃。
- 超时/超请求：先落盘部分结果，`status=partial` 并记录未完成项；`KeyboardInterrupt` 也保留已写结果并退出码 0。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 scripts/main.py full       # 完整流程：scaffold → inventory → finalize
python3 scripts/main.py scaffold   # 仅落盘 partial 骨架（3 分钟内先跑）
python3 scripts/main.py inventory  # 仅盘点
python3 scripts/main.py finalize   # 仅融合并校验 answers.json
python3 scripts/selftest.py        # 自检全部鲁棒性场景
```

详细六维度规则、证据定位规范与复核人复跑步骤见 `references/checklist.md`。
