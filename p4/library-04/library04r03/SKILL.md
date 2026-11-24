---
name: research-kb-qa
description: 科研知识库资料管理与带来源引用的问答 Skill。用于盘点输入文件/目录与权限、提取元数据与版本关系、按内容哈希去重、建立可定位的证据回链，并对多文件问答逐条给出逐字引用与不确定性分级；以确定性脚本保证同输入两次产物一致，交付可复核、可交接、可复跑的清单与操作记录。当需要整理科研资料、维护版本关系或做带引用问答并产出 result.json 时使用。
version: 2.0.0
---

# 科研知识库资料管理与带来源引用的问答

## 1. 角色与何时使用

本 Skill 把“盘点资料 → 组织元数据/版本 → 带来源引用的问答 → 可复核交付”固化为可复跑流程，解决“回答通顺但引用对不上文件/段落/版本”的问题。适用：跨目录、跨格式、含重复副本与历史版本的科研文件集合，需要产出文件清单、元数据、版本关系、哈希证据与带来源引用的问答结果。不适用：无材料的一次性聊天问答；已有专用工具链且无需复核的场景。

核心新增价值：**自检与确定性**。所有内容派生字段（sources、library、answers[].evidence、uncertainty）对同一输入是字节级可复现的；`scripts/selftest.py` 会用两次重跑验证这一点，并校验契约回填与证据逐字命中。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束，可能含 `output_contract`、`questions`、材料路径）与 `/input/task.md`（题目说明）。
- 材料（PDF、Markdown、文本、Office 等）位于 `/input` 下或由 `case.json` 指明的子目录。**必须读实际文件，不依据文件名标签臆测内容。**
- 工作目录 `/app/work`，最终产物写入 `/app/output/`。
- 脚本用相对路径调用（`scripts/core_impl.py`），仅依赖 Python 标准库；缺 `pdftotext` 时自动降级并记录，不伪造 PDF 文本。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：取自 `case.json` 的 case_id/id/challenge_id/armiId。
- `output_contract` 的全部字段：**原样回填全部键**（缺失给 `null`/`[]`/`{}`，不改名、不删除未知键），并保留 `output_contract` 原值；本 Skill 固定键不覆盖未知回填键。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度。
- `sources`：文件与来源清单（相对路径、SHA256、类型、大小、mode 八进制、来源）。
- `uncertainty`：逐条不确定性分级与理由；`pending_human`：仍需人工判定的项。
- `change_log`：每步操作记录（步骤、时间、动作、状态）。
- 另附 `answers`（逐问答案+证据回链）、`library`（六维度清单摘要）、`errors`、`degradations`、`runtime`、`limitations`、`artifact`。
- 附带可读产物：`inventory.md`（清单）、`evidence_map.json`（证据映射）、`selftest_report.json`（自检报告，由 selftest 生成）。

`answers[].evidence[]` 每条必含 `file`（相对路径）、`sha256`、`locator`（`rel#L12-L15` 或 PDF `rel#p3`）、`quote`（逐字片段）、`confidence`。

## 4. 执行流程与预算

总预算：**900 秒、64 次模型请求、单次响应 ≤16384 token**。硬规则：**前 3 分钟内先跑 scaffold 落盘一版合法 result.json 骨架，之后只做增量改进**；任何一步失败不得崩溃、不得编造。

| 阶段 | 时间 | 请求 | 动作 | 输入 → 输出 |
|---|---|---|---|---|
| 0 骨架 | 0–3 min | ~5 | 读 case.json/task.md；`python3 scripts/main.py --phase scaffold` | /input → result.json 骨架 + work/state.json |
| 1 盘点 | 3–12 min | ~20 | `python3 scripts/main.py --phase inventory`；读清单，抽查关键文件 | /input → work/inventory.json + evidence_index.json |
| 2 问答 | 12–14 min | ~15 | 逐问检索→摘录逐字证据→编辑 work/answers.json | 材料+索引 → work/answers.json |
| 3 汇总 | 14–15 min | 剩余 | `python3 scripts/main.py --phase finalize`；`python3 scripts/selftest.py` | answers+inventory → 最终 result.json |

`--phase all` 依次执行 scaffold→inventory→qa→finalize；qa 只在 answers.json 不存在时写检索候选，不覆盖模型已写的答案。

## 5. 证据回链与不确定性（硬规则）

- 每个关键论断必须带 `file`、`sha256`、`locator`、`quote`（原文逐字，禁改写）。
- 不得虚构引用、DOI、数值、页码；拿不到就写 `not_reported` 或 `pending_human`。
- 分级：`certain`（逐字+单一来源）、`likely`（推断/摘要）、`conflict`（来源矛盾，全部记录并标 `source_conflict`）、`insufficient_evidence`（库内无依据）、`unreadable`（不可读/扫描件无文本层/权限不足）。
- 冲突不隐藏：同一指标两来源不一致时两个都记录。

## 6. 自检清单（提交前逐条勾选）

- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] **正确性**：每条答案引用真实、定位准确、`quote` 逐字对应，版本与权限处置正确。
- [ ] **可核验性**：`operation_evidence` 含哈希、版本记录、引用定位，可复跑核对。
- [ ] **不确定性**：证据不足/冲突/不可读/权限受限均分级提示并说明缺什么。
- [ ] **可复现/确定性**：同输入两次运行，屏蔽 `time`/`runtime`/`generated_at` 后其余字段一致（`selftest.py` 自动验证）。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `failures_recovery`，未完成不写完成。
- [ ] **契约回填**：result.json 含 case_id 与 output_contract 全部字段，JSON 合法。
- [ ] **自检通过**：`python3 scripts/selftest.py --input /input --output /app/output --work /app/work` 退出码 0。

## 7. 降级策略

- 空输入目录：`status=insufficient_evidence`，退出码 0，写合法结果并列出缺口。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料。
- 缺 `pdftotext`/OCR：PDF 无文本层标 `unreadable`/`needs_ocr`，不编造文本。
- 写盘失败：降级为最小合法产物 `{"case_id","status":"partial","sources","uncertainty","change_log"}`，不崩溃。
- 超时/超请求：先落盘部分结果，`status=partial` 并记录未完成项；`limitations` 如实标注缺口，绝不编造数据或软件状态。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 scripts/main.py --phase all --input /input --output /app/output --work /app/work
# Agent 在阶段 2 编辑 /app/work/answers.json 补齐答案后：
python3 scripts/main.py --phase finalize --input /input --output /app/output --work /app/work
python3 scripts/selftest.py --input /input --output /app/output --work /app/work
```

详细六维度规则、证据定位规范与确定性屏蔽字段见 `references/checklist.md`。
