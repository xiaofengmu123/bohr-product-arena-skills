---
name: research-kb-evidence
description: 科研知识库资料盘点与带来源引用的问答 Skill（降级与不确定性显式化变体）。用纯标准库脚本盘点文件/目录/权限、按 SHA256 去重、识别版本关系，逐问产出 locator+逐字 quote 的证据回链，并把 uncertainty 分级与 limitations（缺口/不可读/缺软件/权限受限）作为一等字段显式落盘；库内无依据时如实回“无证据”并说明缺什么，绝不编造。当需要整理科研资料、维护版本关系或做可复核的带引用问答并产出 result.json 时使用。
version: 2.0.0
---

# 科研知识库证据化问答（降级与不确定性显式化）

## 1. 角色与何时使用

本 Skill 把“盘点资料 → 六维度组织 → 逐问检索 → 证据回链 → 缺证判断 → 可复核交付”固化为可复跑流程。核心取向：**每个关键论断必须能指回具体文件/版本/段落的逐字片段；知识库里没有依据时，答案要敢说“没有”并写明缺什么**。相比普通资料整理，本版把 `limitations` 与 `uncertainty` 提升为一等显式字段并随每一步增量落盘。

适用：跨目录、跨格式、含重复副本与历史版本的科研文件集合，需要交付文件清单、元数据、版本关系、哈希证据与带来源引用问答，且必须可独立复核、换人可复跑。

不适用：无材料的一次性聊天问答；材料内容不可读且使用者拒绝任何 OCR/人工补录时只能输出 `insufficient_evidence`，不得补造。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束、可能含 `output_contract`/`questions`/材料路径）与 `/input/task.md`（题目说明）。
- 材料位于 `/input` 下或 `case.json` 指明的子目录。**必须以实际文件内容为准，不得依据文件名标签臆测内容。**
- 工作目录 `/app/work`，最终产物写入 `/app/output/`。
- 脚本只用 Python 标准库，以相对路径调用：`scripts/main.py`、`scripts/inventory.py`、`scripts/finalize.py`。
- 读文件统一 `utf-8-sig` 容错；单文件超 2MB 跳过正文解析但仍记录哈希/元数据并写进 `limitations`。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：取自 `case.json` 的 `case_id`/`id`/`challenge_id`，取不到写 `"unknown"` 并在 `limitations` 标注。
- `output_contract` 的全部字段：**原样回填全部键**，不改名、不删除未知键；缺值按类型给 `null`/`[]`/`""`/`{}`。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度；只有问答已落地且证据可回链时才写 `ok`。
- `sources`：文件清单（相对路径、SHA256、类型、大小、编码、权限、版本标记）。
- `uncertainty`：逐条分级 `certain | likely | conflict | inferred | insufficient_evidence | unreadable`，每条附 `reason` 与 `missing`。
- `limitations`：**显式缺口清单**——目录为空、文件不可读、扫描件无文本层、缺 `pdftotext`/OCR、权限受限、编码失败、超 2MB 跳过、契约缺省、人为待定等，逐条写 `limitation`/`impact`/`files`。
- `pending_human`：机器无法定夺、需人工确认的条目（含具体问题与依据）。
- `change_log`：每步记录 `{step,start_utc,end_utc,action,status,detail}`。
- 另附：`answers`（逐问答案+证据回链）、`library`（六维度摘要）、`errors`、`degradations`、`runtime`、`failures_recovery`。
- 附带可读产物：`inventory.md`（清单）、`evidence_map.json`（证据映射）。

`status` 判定硬规则：缺输入 → `input_error`；输入空目录 → `insufficient_evidence`；有产物但存在未解决缺口/答问不全 → `partial`；答问完整且证据可回链 → `ok`。**未完成绝不写“完成”。**

## 4. 执行流程与预算

总预算：**900 秒、64 次模型请求**（单次响应含推理 ≤16384 token）。硬规则：**前 3 分钟内必须先落盘一版合法 `result.json` 骨架（`status=partial`），之后只做增量改进**；任何一步失败不得崩溃、不得编造。

| 阶段 | 时间 | 步骤 | 输入 → 输出 |
|---|---|---|---|
| 0 骨架 | 0–3 min | 读 `/input/case.json`、`/input/task.md`；执行 `scripts/main.py`（内部先 sketch 再 inventory） | /input → `/app/output/result.json`(partial) + `/app/work/` 中间件 |
| 1 盘点 | 3–12 min | 复核 `inventory.json`、`evidence_index.json`，抽查关键文件与可读性 | /input → 六维度清单 + 证据索引 + limitations/uncertainty 种子 |
| 2 问答 | 12–14 min | 逐问检索 → 摘录逐字证据 → 写 `/app/work/answers.json` | 材料+索引 → `answers.json` |
| 3 汇总 | 14–15 min | 执行 `scripts/finalize.py`；按第 6 节自检清单逐条补漏 | answers+inventory → 最终 `/app/output/result.json` |

- 阶段 0 必须“先落盘再分析”：`main.py` 会在盘点前把带 `case_id`/`status=partial`/回填契约键的最小合法骨架写入 `/app/output/result.json`，保证中途异常也不留空产物。
- 阶段 2 的 `/app/work/answers.json` 结构：

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

## 5. 证据溯源与不确定性/limitations（硬规则）

- 每个关键论断必须带 `file`（相对路径）、`sha256`、`locator`（文本 `#L12-L15`；PDF 优先 `#p3`）、`quote`（原文逐字片段，禁止改写）。
- 不得虚构引用、DOI、数值、页码、软件状态；拿不到写 `not_reported` 或放入 `pending_human`。
- 不确定性分级：`certain`（逐字+单一来源）、`likely`（推断/摘要）、`inferred`（跨片段组合推断）、`conflict`（来源矛盾，全部记录并标 `source_conflict`）、`insufficient_evidence`（库内无依据）、`unreadable`（不可读/扫描件无文本层/权限不足）。
- **limitations 与 uncertainty 分开**：`uncertainty` 针对具体断言的可信度，`limitations` 针对运行环境/材料本身的缺口（缺软件、坏编码、空目录、超限跳过、需 OCR），二者都要写清“缺什么、影响什么”。
- 冲突不隐藏：同一指标两来源不一致时两个都记录。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] **正确性**：每条答案引用真实、定位准确、`quote` 逐字对应，版本与权限处置正确。
- [ ] **可核验性**：`operation_evidence` 含哈希、版本记录、引用定位，可让他人复跑核对。
- [ ] **不确定性显式化**：证据不足/冲突/不可读/权限受限均分级提示，并说明缺什么。
- [ ] **limitations 显式化**：缺软件/缺数据/坏编码/超限跳过/空目录逐条记录 `limitation`+`impact`+`files`。
- [ ] **可复现**：同一输入两次运行结果一致（确定性排序、固定哈希、固定时间戳来源）。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `failures_recovery`，未完成不写完成。
- [ ] **契约回填**：result.json 含 case_id 与 output_contract 全部字段，JSON 合法，脚本退出码为 0。
- [ ] **交接物存在**：`result.json`、`inventory.md`、`evidence_map.json` 均已落盘。

## 7. 降级策略（缺数据/缺软件据实标注，不编造）

- 空输入目录：`status=insufficient_evidence`，退出码 0，写合法结果并在 `limitations` 列出缺什么。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料；坏文件标 `unreadable`。
- 缺 `pdftotext`/OCR：PDF 无文本层时标 `unreadable`/`needs_ocr`，写进 `limitations` 与 `uncertainty`，**不编造文本**。
- 权限不足：单个文件 `os.access` 失败即记录 `permission_denied`，不中断整体。
- 写盘失败：降级为最小合法产物 `{"case_id","status":"partial","sources","uncertainty","limitations","change_log"}`，不让进程崩溃。
- 超时/超请求：先落盘部分结果，`status=partial` 并记录未完成项到 `failures_recovery`。
- 任何降级都写入 `degradations`（含触发条件与恢复路径），不掩盖。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
# 阶段 0+1：先落盘 partial 骨架 + 六维度盘点（务必在 3 分钟内执行）
python3 scripts/main.py --input /input --output /app/output --work /app/work
# 阶段 2：Agent 依据 inventory.json / evidence_index.json 检索并写 answers.json
# 阶段 3：合并 answers 与契约，升级 status
python3 scripts/finalize.py --input /input --output /app/output --work /app/work
```

自检：`python3 scripts/main.py --help` 退出码 0；`python3 - <<'PY'` 解析 `result.json` 校验 `case_id`/`status`/契约键/references。

详细六维度规则、证据定位规范与分级细则见 `references/checklist.md` 与 `references/method.md`。