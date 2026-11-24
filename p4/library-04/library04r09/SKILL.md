---
name: research-kb-qa
description: 科研知识库资料管理与带来源引用的问答 Skill。用于盘点输入文件/目录与权限、提取元数据与版本关系、按内容哈希去重、建立可定位的证据回链，并对多文件问答逐条给出逐字引用与不确定性分级。运行时输入固定为 /input/case.json 与 /input/task.md，产物写入 /app/output/result.json；脚本仅依赖 Python 标准库，确定性强、可复跑、可复核。当需要整理科研资料、维护版本关系或做带引用问答并产出 result.json 时使用。
version: 1.1.0
---

# 科研知识库资料管理与带来源引用问答

## 1. 角色与何时使用

本 Skill 把「盘点资料 → 组织元数据/版本 → 带来源引用问答 → 可复核交付」固化为可复跑流程，解决「回答通顺但引用对不上文件/段落/版本」的问题。适用于跨目录、跨格式、含重复副本与历史版本的科研文件集合，需要产出文件清单、六维度组织摘要、哈希证据与逐字引用问答。

不适用于：无材料的一次性聊天问答；已有专用工具链且无需复核的场景。

边界：一个 Skill 只解决「知识库盘点 + 带引用问答」这一类任务，不做通用大而全流程；没有真实材料时输出「材料不足」而非示例填充。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（任务、约束，可能含 `output_contract`）与 `/input/task.md`（题目说明）。
- 材料（PDF、Markdown、文本、Office 等）位于 `/input` 下或其子目录。**必须读实际文件，不依据文件名标签臆测内容**；PDF 用 `pdftotext` 抽取，不信任预提取答案层。
- 工作目录 `/app/work`，最终产物与可读附属文件写入 `/app/output/`。
- 脚本用相对路径调用：`scripts/main.py`、`scripts/selftest.py`；只依赖 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`：取自 `case.json` 的 `case_id` / `id` / `challenge_id`。
- **output_contract 全部字段**：原样回填全部键，不改名、不删除未知键；Agent 未填写时缺省为 `null`，由 `answers.json` 的 `output` 映射覆盖。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实反映完成度。
- `sources`：文件与来源清单（相对路径、SHA256、类型、大小、版本标记）。
- `answers`：逐问答案 + 证据回链（file/sha256/locator/quote/confidence/verified）。
- `library`：六维度摘要（文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源）。
- `uncertainty` / `pending_human` / `errors` / `degradations` / `failures_recovery` / `runtime` / `change_log`。
- 附带可读产物：`evidence_map.json`（证据映射）；`/app/work/inventory.json`（清单）、`evidence_index.json`（检索索引）。

完成标准（与题目一一对应）：① 资料结果——目录/清单/元数据/版本关系与输入文件和规则对应；② 操作证据——哈希、来源、权限、变更与日志可复跑核对；③ 失败与恢复——跳过、冲突、无权限、重复与恢复均记录，不把未完成写成完成。

## 4. 执行流程与预算

总预算：**900 秒、64 次模型请求、每次 16384 tokens（含推理）**。硬规则：**前 3 分钟内先跑 `scaffold` 落盘一版合法 `result.json` 骨架，之后只做增量改进**；任何一步失败不得让进程崩溃，也不得编造数据。

| 阶段 | 时间 | 请求 | 步骤 | 输入 → 输出 |
|---|---|---|---|---|
| 0 骨架 | 0–3 min | ~5 | 读 `/input/case.json`、`/input/task.md`；`python3 scripts/main.py scaffold` | /input → `/app/output/result.json` + `/app/work/case_summary.json` |
| 1 盘点 | 3–12 min | ~20 | `python3 scripts/main.py inventory`；读清单与索引；抽查关键文件 | /input → `/app/work/inventory.json` + `evidence_index.json` |
| 2 问答 | 12–14 min | ~15 | 逐问检索→摘录逐字证据→写 `/app/work/answers.json` | 材料+索引 → `/app/work/answers.json` |
| 3 汇总 | 14–15 min | 剩余 | `python3 scripts/main.py finalize --answers /app/work/answers.json`；按第 6 节补漏 | answers+inventory → 最终 `/app/output/result.json` |

`answers.json` 结构：

```json
{
  "output": {"<output_contract 键>": "<该键的值>"},
  "answers": [
    {"question": "...", "answer": "...",
     "evidence": [{"file": "materials/paper.md", "sha256": "...",
                   "locator": "materials/paper.md#L12-L15", "quote": "逐字片段",
                   "confidence": "certain"}],
     "uncertainty": "certain", "missing": null}
  ],
  "uncertainty": [{"level": "conflict", "item": "...", "reason": "两来源不一致", "sources": ["a", "b"]}]
}
```

## 5. 证据溯源与不确定性（硬规则）

- 每个关键论断必须带 `file`（相对路径）、`sha256`、`locator`（文本 `#L起-L止`、PDF `#p页码`）、`quote`（原文逐字片段）；`finalize` 会回读原文件校验 quote 是否命中，未命中记入 `failures_recovery`。
- 不得虚构引用、DOI、数值、页码；拿不到写 `not_reported` 或列入 `pending_human`。
- 不确定性分级：`certain`（逐字+单一来源）、`likely`（推断/摘要）、`conflict`（来源矛盾，全部记录并标 `source_conflict`）、`insufficient_evidence`（库内无依据）、`unreadable`（不可读/扫描件无文本层/权限不足）。
- 冲突不隐藏：同一指标两来源不一致时两个都记录。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] **完整性**：`library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] **正确性**：每条答案引用真实、定位准确、`quote` 逐字对应；`finalize` 的 `verified` 全部为 `true`。
- [ ] **可核验性**：`sources` 含哈希、`evidence_map.json` 含引用定位，可让他人复跑核对。
- [ ] **不确定性**：证据不足/冲突/不可读/权限受限均分级提示并说明缺什么。
- [ ] **可复现**：同一输入两次运行结果一致（确定性排序、固定哈希、去除时间戳后逐字节相同）。
- [ ] **失败记录**：跳过/冲突/无权限/重复/恢复写入 `failures_recovery`；未完成不写完成。
- [ ] **契约回填**：result.json 含 `case_id` 与 output_contract 全部字段，JSON 合法。
- [ ] **自检通过**：`python3 scripts/selftest.py` 打印 `SELFTEST PASSED`。

## 7. 降级策略

- 空输入目录：`status=insufficient_evidence`，退出码 0，写合法结果并列出缺口。
- 坏 JSON/坏编码：捕获写入 `errors`/`degradations`，继续处理其余材料。
- 缺 `pdftotext`/OCR：PDF 无文本层标 `unreadable` + `needs_ocr`，不编造文本。
- 写盘失败：降级为最小合法产物 `{"case_id","status":"partial","sources","uncertainty","change_log"}`，不让进程崩溃。
- 超时/超请求：先落盘部分结果，`status=partial` 并记录未完成项。
- 所有缺失/降级信息如实写入 `degradations` 与 `pending_human`，**不得编造软件状态或科学数据**。

## 8. 脚本调用与最小示例（示意，非正式案例结果）

```bash
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py inventory --input /input --work /app/work
# Agent 按第 4 阶段写 /app/work/answers.json
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work --answers /app/work/answers.json
```

`main.py` 缺省参数即 `/input`、`/app/output`、`/app/work`、`/app/work/answers.json`，未知参数容错忽略；`scaffold`/`inventory`/`finalize` 三个子命令都可单独复跑。复核人可执行 `python3 scripts/selftest.py` 验证完整性与确定性。

示意 `answers.json`（标明为示例，非正式案例结果）：

```json
{
  "output": {"questions": [{"q": "存活率", "a": "90%"}], "summary": "示例摘要"},
  "answers": [
    {"question": "细胞存活率是多少？",
     "answer": "细胞存活率 90%。",
     "evidence": [{"file": "materials/v1/paper.md",
                   "sha256": "真实 SHA256，见 inventory.json",
                   "locator": "materials/v1/paper.md#L5-L6",
                   "quote": "细胞存活率 90%",
                   "confidence": "certain"}],
     "uncertainty": "certain", "missing": null}
  ],
  "uncertainty": []
}
```
